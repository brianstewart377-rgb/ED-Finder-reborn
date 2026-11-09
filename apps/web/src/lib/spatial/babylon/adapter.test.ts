import { describe, expect, it, vi } from 'vitest';
import { PickingInfo } from '@babylonjs/core/Collisions/pickingInfo.js';
import { NullEngine } from '@babylonjs/core/Engines/nullEngine.js';
import { Matrix } from '@babylonjs/core/Maths/math.vector.js';
import { StandardMaterial } from '@babylonjs/core/Materials/standardMaterial.js';
import { systemDetailFromV3, type ExploreSystem } from '$lib/api/client';
import { v3Body, v3System } from '$lib/api/v3-system-fixtures';
import { parseId64 } from '$lib/domain/id64';

import type {
  GalaxySceneContract,
  GalaxySystemPoint,
  SpatialContribution,
  SystemSceneContract,
} from '../contracts';
import { buildFixtureCatalogueDensity } from '../galaxy-density.fixture';
import { createCatalogueDensityContribution } from '../galaxy-density';
import { createCatalogueStarsContribution } from '../galaxy-star-stream';
import {
  DENSITY_CROSSFADE_FAR_LY,
  DENSITY_CROSSFADE_NEAR_LY,
} from '../galaxy-density-crossfade';
import { createCommanderHistoryContribution } from '../commander-history';
import {
  buildExploreGalaxyScene,
  mapExploreSelectedSystemPoint,
} from '../explore-scene';
import {
  createGalaxyNebulaeContribution,
  type GalaxyNebulaePayload,
} from '../galaxy-nebulae';
import type { SpatialBackendSession } from '../lifecycle';
import { buildSystemScene } from '../system-scene';
import {
  createBabylonGalaxyScene,
  createBabylonSession,
  createBabylonSystemScene,
  createBabylonSpatialRuntime,
  commanderHistoryHeatColour,
  galaxyStarMarkerSizeLy,
  nebulaCloudRadiusLy,
  subscribeToBabylonResourceEvents,
  type BabylonRuntimeDependencies,
} from './adapter';

const createSession = (backend: 'WEBGPU' | 'WEBGL2') =>
  ({
    backend,
    resize: vi.fn(),
    render: vi.fn(),
    dispose: vi.fn(),
  }) satisfies SpatialBackendSession;

const batchCataloguePoint: GalaxySystemPoint = {
  systemId64: '42',
  name: 'Catalogue black hole',
  positionLy: { x: 0, y: 0, z: 0 },
  primaryStar: { type: 'Black Hole' },
};
const batchFinderPoint: GalaxySystemPoint = {
  systemId64: '9007199254740993',
  name: 'Finder black hole',
  positionLy: { x: 0, y: 0, z: 0 },
  primaryStar: { type: 'Black Hole' },
};

function batchSystemContribution(
  owner: 'CATALOGUE' | 'FINDER',
  systems: readonly GalaxySystemPoint[],
): SpatialContribution {
  return {
    id: owner === 'FINDER' ? 'finder-results' : 'catalogue-viewport-stars',
    owner,
    revision: 1,
    layers: [
      {
        id: owner === 'FINDER' ? 'finder-systems' : 'catalogue-systems',
        version: 1,
        representation: 'AUTHORITATIVE',
        targetCount: systems.length,
        truncated: false,
        payload: { systems },
      },
    ],
  };
}

function densityBatchScene(
  distanceLy: number,
  catalogue: readonly GalaxySystemPoint[] = [batchCataloguePoint],
  finder: readonly GalaxySystemPoint[] = [batchFinderPoint],
): GalaxySceneContract {
  const density = buildFixtureCatalogueDensity(catalogue, {
    generationId: 'fixture:adapter-independent-batches-v1',
    pyramidVersion: 'fixture-pyramid-v1',
    level: 2,
    cellSizeLy: 100,
    cellOriginLy: { x: -1_000, y: -1_000, z: -1_000 },
    boundsLy: {
      min: { x: -1_000, y: -1_000, z: -1_000 },
      max: { x: 1_000, y: 1_000, z: 1_000 },
    },
    coverageAsOf: '2026-09-13T00:00:00Z',
  });
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
    contributions: [
      createCatalogueDensityContribution(density, 1),
      batchSystemContribution('CATALOGUE', catalogue),
      batchSystemContribution('FINDER', finder),
    ],
  };
}

describe('Babylon spatial adapter boundary', () => {
  it.each([
    { reservedX: 1, expectedId: '2' },
    { reservedX: 1_000, expectedId: '1' },
  ])(
    'prefers reserved catalogue picks only inside the existing screen tolerance ($reservedX LY)',
    ({ reservedX, expectedId }) => {
      const engine = new NullEngine({
        renderWidth: 640,
        renderHeight: 360,
        textureSize: 512,
        deterministicLockstep: true,
        lockstepMaxSteps: 1,
      });
      const session = createBabylonSession(engine, 'WEBGL2');
      const events = vi.fn();
      const contract: GalaxySceneContract = {
        kind: 'galaxy',
        revision: 1,
        camera: {
          focusLy: { x: 0, y: 0, z: 0 },
          distanceLy: 100,
          bearingRad: 0,
          pitchRad: 0.6,
          projection: 'perspective',
          revision: 1,
        },
        selection: [],
        contributions: [
          createCatalogueStarsContribution(
            [
              {
                id64: parseId64('1'),
                name: 'Background',
                x: 0,
                y: 0,
                z: 0,
                populated: false,
              },
            ],
            1,
            false,
            [
              {
                systemId64: '2',
                name: 'Reserved',
                positionLy: { x: reservedX, y: 0, z: 0 },
              },
            ],
          ),
        ],
      };
      session.execute?.({ type: 'LOAD_SCENE', scene: contract }, events);
      const scene = engine.scenes.at(-1)!;
      scene.render();
      // The exact ray encounters background detail. A nearby known target
      // still wins; a reserved target outside the tolerance cannot steal it.
      vi.spyOn(scene, 'pick').mockReturnValue({
        hit: true,
        pickedMesh: scene.getMeshByName('finder-system-instances'),
        thinInstanceIndex: 1,
      } as ReturnType<typeof scene.pick>);
      session.execute?.({ type: 'PICK', screenX: 320, screenY: 180 }, events);
      expect(events).toHaveBeenCalledWith({
        type: 'TARGET_PICKED',
        target: { kind: 'system', systemId64: expectedId },
      });
      session.dispose();
      engine.dispose();
    },
  );

  it('retains reserved picking priority when Finder supplies the richer duplicate point', () => {
    const finder = {
      id64: parseId64('42'),
      name: 'Finder target',
      coords: { x: 10, y: 0, z: 20 },
      population: 12_345,
    } as ExploreSystem;
    const initial = buildExploreGalaxyScene([finder], null, 1);
    const engine = new NullEngine();
    const product = createBabylonGalaxyScene(engine, {
      ...initial,
      contributions: [
        ...initial.contributions,
        createCatalogueStarsContribution([], 1, false, [
          {
            systemId64: '42',
            name: 'Pinned target',
            positionLy: { x: 10, y: 0, z: 20 },
          },
        ]),
      ],
    });
    expect(product.points).toHaveLength(1);
    expect(product.points[0]).toMatchObject({
      name: 'Finder target',
      reserved: true,
      summary: { population: 12_345 },
    });
    product.scene.dispose();
    engine.dispose();
  });

  it('keeps reserved catalogue targets in the always-visible batch behind density', () => {
    const initial = buildExploreGalaxyScene([], null, 1);
    const engine = new NullEngine();
    const product = createBabylonGalaxyScene(engine, {
      ...initial,
      camera: { ...initial.camera, distanceLy: DENSITY_CROSSFADE_FAR_LY + 1 },
      contributions: [
        ...initial.contributions,
        createCatalogueStarsContribution(
          [
            {
              id64: parseId64('7'),
              name: 'Background',
              x: 0,
              y: 0,
              z: 0,
              populated: false,
            },
          ],
          1,
          false,
          [
            {
              systemId64: '42',
              name: 'Pinned target',
              positionLy: { x: 10, y: 0, z: 20 },
            },
          ],
        ),
      ],
    });
    // The pinned (reserved) point is not a Finder result or a selection, yet it
    // must stay pickable and visible when the catalogue batch fades out.
    expect(product.finderStars.points.map((point) => point.systemId64)).toEqual(
      ['42'],
    );
    expect(
      product.catalogueStars.points.map((point) => point.systemId64),
    ).toEqual(['7']);
    product.scene.dispose();
    engine.dispose();
  });

  it.each(['CATALOGUE', 'FINDER'] as const)(
    'reports the id64 and name when hovering a pickable %s system',
    (owner) => {
      const engine = new NullEngine({
        renderWidth: 640,
        renderHeight: 360,
        textureSize: 512,
        deterministicLockstep: true,
        lockstepMaxSteps: 1,
      });
      const session = createBabylonSession(engine, 'WEBGL2');
      const events = vi.fn();
      const point = {
        systemId64: '9007199254740993',
        name: 'Hover catalogue star',
        positionLy: { x: 0, y: 0, z: 0 },
        primaryStar: { type: 'G2' },
      };
      const density = buildFixtureCatalogueDensity([point], {
        generationId: 'fixture:hover-star-density',
        pyramidVersion: 'fixture-pyramid-v1',
        level: 2,
        cellSizeLy: 100,
        cellOriginLy: { x: -1_000, y: -1_000, z: -1_000 },
        boundsLy: {
          min: { x: -1_000, y: -1_000, z: -1_000 },
          max: { x: 1_000, y: 1_000, z: 1_000 },
        },
        coverageAsOf: '2026-11-02T00:00:00Z',
      });
      const contract: GalaxySceneContract = {
        kind: 'galaxy',
        revision: 1,
        camera: {
          focusLy: point.positionLy,
          distanceLy: 500,
          bearingRad: 0,
          pitchRad: 0.5,
          projection: 'perspective',
          revision: 1,
        },
        selection: [],
        contributions: [
          createCatalogueDensityContribution(density, 1),
          {
            id: 'hover-systems',
            owner,
            revision: 1,
            layers: [
              {
                id:
                  owner === 'CATALOGUE'
                    ? 'catalogue-systems'
                    : 'finder-systems',
                version: 1,
                representation: 'AUTHORITATIVE',
                targetCount: 1,
                truncated: false,
                payload: { systems: [point] },
              },
            ],
          },
        ],
      };
      try {
        session.execute?.({ type: 'LOAD_SCENE', scene: contract }, events);
        const scene = engine.scenes.at(-1)!;
        const updateTransforms = () => {
          scene.updateTransformMatrix(true);
          for (const mesh of scene.meshes) mesh.computeWorldMatrix(true);
        };
        updateTransforms();
        const hover = { type: 'HOVER', screenX: 320, screenY: 180 } as const;
        events.mockClear();
        session.execute?.(hover, events);
        expect(events).toHaveBeenCalledWith({
          type: 'TARGET_HOVERED',
          target: {
            kind: 'system',
            systemId64: point.systemId64,
            name: point.name,
          },
          screenX: 320,
          screenY: 180,
        });
        // Stable pointer movement does not repeatedly invalidate label candidates.
        events.mockClear();
        session.execute?.(hover, events);
        expect(events).not.toHaveBeenCalled();
        session.execute?.({ type: 'CLEAR_HOVER' }, events);
        expect(events).toHaveBeenCalledWith({ type: 'TARGET_HOVERED' });

        session.execute?.(
          {
            type: 'SET_CAMERA',
            camera: {
              ...contract.camera,
              distanceLy: DENSITY_CROSSFADE_FAR_LY + 1,
              revision: 2,
            },
          },
          events,
        );
        updateTransforms();
        events.mockClear();
        session.execute?.(hover, events);
        if (owner === 'CATALOGUE') {
          // Fully faded catalogue stars cannot intercept hover through density.
          expect(events).not.toHaveBeenCalled();
        } else {
          expect(events).toHaveBeenCalledWith(
            expect.objectContaining({
              type: 'TARGET_HOVERED',
              target: {
                kind: 'system',
                systemId64: point.systemId64,
                name: point.name,
              },
            }),
          );
        }
      } finally {
        session.dispose();
        engine.dispose();
      }
    },
  );

  it('keeps the selected marker when a replacement page omits the selected Finder system', () => {
    const selected = {
      id64: parseId64('9007199254740993'),
      name: 'Selected Reach',
      coords: { x: 12, y: -4, z: 80 },
    } as ExploreSystem;
    const next = {
      id64: parseId64('42'),
      name: 'Next page',
      coords: { x: 20, y: 3, z: 90 },
    } as ExploreSystem;
    const engine = new NullEngine();
    const session = createBabylonSession(engine, 'WEBGL2');
    const events = vi.fn();
    const initial = buildExploreGalaxyScene([selected], selected.id64, 1);
    expect(
      session.execute?.({ type: 'LOAD_SCENE', scene: initial }, events),
    ).toEqual({ status: 'executed' });
    const nextScene = buildExploreGalaxyScene([next], selected.id64, 2, {
      selectedPoint: mapExploreSelectedSystemPoint(selected),
      camera: initial.camera,
    });
    expect(
      session.execute?.({ type: 'LOAD_SCENE', scene: nextScene }, events),
    ).toEqual({ status: 'executed' });
    const scene = engine.scenes.at(-1)!;
    const marker = scene.getMeshByName('selected-system-marker');
    expect(marker).not.toBeNull();
    expect(marker?.position.asArray()).toEqual([12, -4, 80]);
    expect(
      scene.meshes.filter((mesh) => mesh.name === 'selected-system-marker'),
    ).toHaveLength(1);
    const returned = buildExploreGalaxyScene([selected], selected.id64, 3, {
      selectedPoint: mapExploreSelectedSystemPoint(selected),
      camera: initial.camera,
    });
    expect(returned.contributions).toHaveLength(1);
    expect(
      session.execute?.({ type: 'LOAD_SCENE', scene: returned }, events),
    ).toEqual({ status: 'executed' });
    expect(
      engine.scenes
        .at(-1)!
        .getMeshByName('selected-system-marker')
        ?.position.asArray(),
    ).toEqual([12, -4, 80]);
    session.dispose();
    engine.dispose();
  });

  it('renders an interactive three-dimensional system scene with pickable bodies and schematic orbits', () => {
    const engine = new NullEngine();
    const contract: SystemSceneContract = {
      kind: 'system',
      revision: 3,
      systemId64: '42',
      fidelity: 'S1',
      camera: {
        systemId64: '42',
        focus: { kind: 'system', systemId64: '42' },
        semanticDistance: 32,
        bearingRad: -0.8,
        pitchRad: 0.56,
        revision: 3,
      },
      bodies: [
        {
          ref: { systemId64: '42', bodyId: 0 },
          name: 'Test Star',
          class: { value: 'Star', representation: 'AUTHORITATIVE' },
          subtype: {
            value: 'G (White-Yellow) Star',
            representation: 'AUTHORITATIVE',
          },
          distanceFromArrivalLs: {
            value: 0,
            representation: 'AUTHORITATIVE',
          },
          displayRadius: 2.2,
          orbital: { placement: 'DETERMINISTIC_SCHEMATIC' },
          rings: { state: 'ABSENT', bands: [] },
        },
        {
          ref: { systemId64: '42', bodyId: 2 },
          parent: { systemId64: '42', bodyId: 0 },
          name: 'Test Giant',
          class: { value: 'Planet', representation: 'AUTHORITATIVE' },
          bodyFamily: { family: 'gas-giant-life', variant: 'water' },
          subtype: {
            value: 'Gas giant with water based life',
            representation: 'AUTHORITATIVE',
          },
          distanceFromArrivalLs: {
            value: 12_000,
            representation: 'AUTHORITATIVE',
          },
          displayRadius: 1.1,
          orbital: { placement: 'DETERMINISTIC_SCHEMATIC' },
          rings: { state: 'PRESENT', bands: [] },
        },
      ],
      infrastructure: [],
      contributions: [],
    };

    const product = createBabylonSystemScene(engine, contract);
    expect(product.bodies).toHaveLength(2);
    expect(product.bodies.every((body) => body.mesh.isPickable)).toBe(true);
    expect(product.bodies[1]?.ringMeshes).toHaveLength(1);
    expect(product.orbitMesh?.metadata).toMatchObject({
      spatialLayer: {
        representation: 'SCHEMATIC',
        meaning: 'known-parent-groups-not-observed-orbital-elements',
      },
    });
    expect(product.scene.metadata).toMatchObject({
      spatialScene: {
        kind: 'system',
        bodyCount: 2,
        visualProfile: {
          threeDimensional: true,
          physicalScale: false,
        },
      },
    });

    product.scene.dispose();
    engine.dispose();
  });

  it('renders family colours, illustrative giant bands and a dim unknown class', () => {
    const engine = new NullEngine();
    const contract = buildSystemScene(
      systemDetailFromV3(
        v3System(),
        ['Earth-like world', 'Class I gas giant', 'Icy body', null].map(
          (bodyClass, index) =>
            v3Body({
              body_id: String(index),
              frontier_body_id: String(index),
              body_type: 'planet',
              // A known arrival distance keeps each body a placed sphere.
              distance_from_arrival_ls: 100 * (index + 1),
              // Even a suggestive name must not invent a class.
              name: 'Earth-like world',
              body_class: bodyClass,
              body_class_source: bodyClass ? 'ratings-v4-derived' : null,
            }),
        ),
        [],
        [],
      ),
      1,
    );
    const product = createBabylonSystemScene(engine, contract);
    const [earth, giant, icy, unknown] = product.bodies;
    const materials = product.bodies.map((body) => {
      expect(body.mesh.material).toBeInstanceOf(StandardMaterial);
      return body.mesh.material as StandardMaterial;
    });
    expect(
      new Set(materials.map((material) => material.diffuseColor.toHexString()))
        .size,
    ).toBe(4);
    expect(
      materials.slice(0, 3).every((material) => material.alpha === 1),
    ).toBe(true);
    expect(materials[3]!.diffuseColor.asArray()).toEqual([0.48, 0.48, 0.52]);
    expect(materials[3]!.alpha).toBe(0.42);
    expect(materials[3]!.needAlphaBlendingForMesh(unknown!.mesh)).toBe(true);
    expect(unknown!.mesh.metadata).toMatchObject({
      bodyFamily: { family: 'unknown' },
      classTreatment: 'unknown-dim',
    });
    expect(unknown!.mesh.isPickable).toBe(true);
    expect(earth!.mesh.getVerticesData('color')).toBeNull();
    expect(icy!.mesh.getVerticesData('color')).toBeNull();
    const bands = giant!.mesh.getVerticesData('color')!;
    expect(bands).not.toBeNull();
    const tones = bands.filter((_, index) => index % 4 === 0);
    expect(Math.min(...tones)).toBeGreaterThanOrEqual(0.79);
    expect(Math.max(...tones)).toBeLessThanOrEqual(1);
    expect(Math.max(...tones) - Math.min(...tones)).toBeGreaterThan(0.15);
    expect(giant!.mesh.getTotalVertices()).toBe(earth!.mesh.getTotalVertices());
    product.scene.dispose();
    engine.dispose();
  });

  it('accepts a system contract through the shared runtime session', () => {
    const engine = new NullEngine();
    const session = createBabylonSession(engine, 'WEBGL2');
    const events = vi.fn();
    const contract: SystemSceneContract = {
      kind: 'system',
      revision: 21,
      systemId64: '99',
      fidelity: 'S0',
      camera: {
        systemId64: '99',
        focus: { kind: 'system', systemId64: '99' },
        semanticDistance: 24,
        bearingRad: 0,
        pitchRad: 0.5,
        revision: 21,
      },
      bodies: [],
      infrastructure: [],
      contributions: [],
    };

    expect(
      session.execute?.({ type: 'LOAD_SCENE', scene: contract }, events),
    ).toEqual({ status: 'executed' });
    expect(events).toHaveBeenCalledWith({
      type: 'CAMERA_CHANGED',
      camera: contract.camera,
    });
    expect(
      session.execute?.(
        {
          type: 'SET_CAMERA',
          camera: { ...contract.camera, semanticDistance: 18, revision: 22 },
        },
        events,
      ),
    ).toEqual({ status: 'executed' });

    session.dispose();
  });

  it('renders exactly the accepted catalogue cells and exposes a rendered-layer receipt', () => {
    const payload = buildFixtureCatalogueDensity(
      [
        { positionLy: { x: 10, y: -5, z: 20 } },
        { positionLy: { x: 14, y: -3, z: 25 } },
        { positionLy: { x: 420, y: 20, z: -200 } },
      ],
      {
        generationId: 'fixture:adapter-density-v1',
        pyramidVersion: 'fixture-pyramid-v1',
        level: 2,
        cellSizeLy: 100,
        cellOriginLy: { x: -1_000, y: -1_000, z: -1_000 },
        boundsLy: {
          min: { x: -1_000, y: -1_000, z: -1_000 },
          max: { x: 1_000, y: 1_000, z: 1_000 },
        },
        coverageAsOf: '2026-09-13T00:00:00Z',
      },
    );
    const sceneContract: GalaxySceneContract = {
      kind: 'galaxy',
      revision: 14,
      camera: {
        focusLy: { x: 0, y: 0, z: 0 },
        distanceLy: 2_500,
        bearingRad: 0,
        pitchRad: 0.5,
        projection: 'perspective',
        revision: 14,
      },
      selection: [],
      contributions: [createCatalogueDensityContribution(payload, 14)],
    };
    const engine = new NullEngine({
      renderWidth: 640,
      renderHeight: 360,
      textureSize: 512,
      deterministicLockstep: true,
      lockstepMaxSteps: 1,
    });

    const product = createBabylonGalaxyScene(engine, sceneContract);

    expect(product.densityMesh).not.toBeNull();
    expect(product.densityMesh?.thinInstanceCount).toBe(payload.cells.length);
    expect(product.densityMesh?.isPickable).toBe(false);
    // Blooming the already-soft glow drew every splat at full emissive into
    // the glow target and laid a flat grey plateau over the disc.
    expect(product.visualGlow.hasMesh(product.densityMesh!)).toBe(false);
    expect(product.renderedLayers).toEqual([
      {
        contributionId: 'catalogue-density-base',
        contributionRevision: 14,
        layerId: 'catalogue-density',
        layerVersion: 1,
        representation: 'DERIVED',
        acceptedTargetCount: payload.cells.length,
        renderedTargetCount: payload.cells.length,
        sourceGeneration: 'fixture:adapter-density-v1',
      },
    ]);
    expect(product.densityMesh?.metadata).toMatchObject({
      spatialLayer: {
        id: 'catalogue-density',
        sourceSystemCount: 3,
        coveredSystemCount: 3,
        renderedCellCount: payload.cells.length,
        generatedOccupancy: false,
      },
    });
    expect(product.scene.getMeshByName('volumetric-galaxy')).toBeNull();
    expect(product.referenceGrid.spec.nominalPlaneY).toBe(0);
    expect(product.referenceGrid.spec.majorStepLy).toBe(
      product.referenceGrid.spec.minorStepLy * 5,
    );
    expect(product.referenceGrid.minorMesh?.metadata).toMatchObject({
      spatialLayer: {
        id: 'galaxy-reference-grid',
        representation: 'SCHEMATIC',
        affectsSystemPositions: false,
      },
    });

    product.scene.dispose();
    engine.dispose();
  });

  it('renders no density geometry when the factual layer is absent', () => {
    const engine = new NullEngine();
    const sceneContract: GalaxySceneContract = {
      kind: 'galaxy',
      revision: 15,
      camera: {
        focusLy: { x: 0, y: 0, z: 0 },
        distanceLy: 80,
        bearingRad: 0,
        pitchRad: 0.5,
        projection: 'perspective',
        revision: 15,
      },
      selection: [],
      contributions: [],
    };

    const product = createBabylonGalaxyScene(engine, sceneContract);

    expect(product.densityMesh).toBeNull();
    expect(product.renderedLayers).toEqual([]);
    expect(product.scene.getMeshByName('catalogue-density-cells')).toBeNull();

    product.scene.dispose();
    engine.dispose();
  });

  it('keeps stars fully opaque when zoomed out with no density layer (inert path)', () => {
    const engine = new NullEngine();
    const sceneContract: GalaxySceneContract = {
      kind: 'galaxy',
      revision: 1,
      camera: {
        focusLy: { x: 0, y: 0, z: 0 },
        distanceLy: DENSITY_CROSSFADE_FAR_LY + 1,
        bearingRad: 0,
        pitchRad: 0.5,
        projection: 'perspective',
        revision: 1,
      },
      selection: [],
      contributions: [],
    };

    const product = createBabylonGalaxyScene(engine, sceneContract);

    expect(product.densityMesh).toBeNull();
    expect(
      product.scene.getMeshByName('finder-system-instances')?.material?.alpha,
    ).toBeCloseTo(1, 5);

    product.scene.dispose();
    engine.dispose();
  });

  it('cross-fades density and star presentations as the camera zooms through the semantic band', () => {
    const payload = buildFixtureCatalogueDensity(
      [
        { positionLy: { x: 10, y: -5, z: 20 } },
        { positionLy: { x: 14, y: -3, z: 25 } },
      ],
      {
        generationId: 'fixture:adapter-crossfade-v1',
        pyramidVersion: 'fixture-pyramid-v1',
        level: 2,
        cellSizeLy: 100,
        cellOriginLy: { x: -1_000, y: -1_000, z: -1_000 },
        boundsLy: {
          min: { x: -1_000, y: -1_000, z: -1_000 },
          max: { x: 1_000, y: 1_000, z: 1_000 },
        },
        coverageAsOf: '2026-09-13T00:00:00Z',
      },
    );
    const baseCamera: GalaxySceneContract['camera'] = {
      focusLy: { x: 0, y: 0, z: 0 },
      distanceLy: 2_500,
      bearingRad: 0,
      pitchRad: 0.5,
      projection: 'perspective',
      revision: 1,
    };
    const sceneContract: GalaxySceneContract = {
      kind: 'galaxy',
      revision: 1,
      camera: baseCamera,
      selection: [],
      contributions: [createCatalogueDensityContribution(payload, 1)],
    };

    const engine = new NullEngine();
    const session = createBabylonSession(engine, 'WEBGL2');
    const events = vi.fn();

    expect(
      session.execute?.({ type: 'LOAD_SCENE', scene: sceneContract }, events),
    ).toEqual({ status: 'executed' });

    const scene = engine.scenes[engine.scenes.length - 1]!;
    const densityMesh = scene.getMeshByName('catalogue-density-cells');
    const starMesh = scene.getMeshByName('finder-system-instances');
    expect(densityMesh).not.toBeNull();
    expect(starMesh).not.toBeNull();

    const setDistance = (distanceLy: number, revision: number): void => {
      expect(
        session.execute?.(
          {
            type: 'SET_CAMERA',
            camera: { ...baseCamera, distanceLy, revision },
          },
          events,
        ),
      ).toEqual({ status: 'executed' });
    };

    setDistance(DENSITY_CROSSFADE_NEAR_LY - 1, 2);
    expect(densityMesh?.material?.alpha).toBeCloseTo(0, 5);
    expect(starMesh?.material?.alpha).toBeCloseTo(1, 5);

    setDistance((DENSITY_CROSSFADE_NEAR_LY + DENSITY_CROSSFADE_FAR_LY) / 2, 3);
    expect(densityMesh?.material?.alpha).toBeCloseTo(0.45, 5);
    expect(starMesh?.material?.alpha).toBeCloseTo(0.5, 5);

    setDistance(DENSITY_CROSSFADE_FAR_LY + 1, 4);
    expect(densityMesh?.material?.alpha).toBeCloseTo(0.9, 5);
    expect(starMesh?.material?.alpha).toBeCloseTo(0, 5);

    session.dispose();
    engine.dispose();
  });

  it('reuses one grid line material across zoom rebuilds, so no shader is recompiled', () => {
    const engine = new NullEngine();
    const session = createBabylonSession(engine, 'WEBGL2');
    const events = vi.fn();
    const camera: GalaxySceneContract['camera'] = {
      focusLy: { x: 0, y: 0, z: 0 },
      distanceLy: 1_000,
      bearingRad: 0,
      pitchRad: 0.5,
      projection: 'perspective',
      revision: 1,
    };
    session.execute?.(
      {
        type: 'LOAD_SCENE',
        scene: {
          kind: 'galaxy',
          revision: 1,
          camera,
          selection: [],
          contributions: [],
        },
      },
      events,
    );
    const scene = engine.scenes.at(-1)!;
    const before = scene.getMeshByName('galaxy-reference-grid-major')!;
    const material = before.material!;
    expect(material).not.toBeNull();
    for (const [revision, distanceLy] of [
      [2, 20_000],
      [3, 400],
    ] as const) {
      session.execute?.(
        { type: 'SET_CAMERA', camera: { ...camera, distanceLy, revision } },
        events,
      );
      const grid = [
        'galaxy-reference-grid-minor',
        'galaxy-reference-grid-major',
        'galaxy-reference-grid-axes',
      ]
        .map((name) => scene.getMeshByName(name))
        .filter((mesh) => mesh !== null);
      expect(grid.length).toBeGreaterThan(0);
      // The camera change rebuilt the grid geometry, but every family still
      // draws with the one material, which was never disposed.
      expect(before.isDisposed()).toBe(true);
      // Identity checks without printing Babylon objects on failure.
      for (const mesh of grid) expect(mesh.material === material).toBe(true);
      expect(scene.materials.includes(material)).toBe(true);
    }

    session.dispose();
  });

  it('keeps the previous frame until a new scene is ready, but never waits forever', () => {
    const engine = new NullEngine();
    const session = createBabylonSession(engine, 'WEBGL2');
    session.execute?.(
      {
        type: 'LOAD_SCENE',
        scene: {
          kind: 'galaxy',
          revision: 1,
          camera: {
            focusLy: { x: 0, y: 0, z: 0 },
            distanceLy: 1_000,
            bearingRad: 0,
            pitchRad: 0.5,
            projection: 'perspective',
            revision: 1,
          },
          selection: [],
          contributions: [],
        },
      },
      vi.fn(),
    );
    const scene = engine.scenes.at(-1)!;
    const draw = vi.spyOn(scene, 'render');
    const ready = vi.spyOn(scene, 'isReady').mockReturnValue(false);
    // Not ready: present nothing and ask for another frame.
    expect(session.render()).toBe(true);
    expect(draw).not.toHaveBeenCalled();
    ready.mockReturnValue(true);
    session.render();
    expect(draw).toHaveBeenCalledOnce();

    // A material that never becomes ready must not stop drawing for good.
    session.execute?.(
      {
        type: 'SET_CAMERA',
        camera: {
          focusLy: { x: 10, y: 0, z: 0 },
          distanceLy: 1_000,
          bearingRad: 0,
          pitchRad: 0.5,
          projection: 'perspective',
          revision: 2,
        },
      },
      vi.fn(),
    );
    ready.mockReturnValue(false);
    draw.mockClear();
    let attempts = 0;
    while (draw.mock.calls.length === 0 && attempts < 1_000) {
      session.render();
      attempts += 1;
    }
    expect(draw).toHaveBeenCalledOnce();
    expect(attempts).toBeGreaterThan(1);
    expect(attempts).toBeLessThanOrEqual(121);

    session.dispose();
  });

  it('applies the density cross-fade immediately on LOAD_SCENE, without waiting for a camera move', () => {
    const payload = buildFixtureCatalogueDensity(
      [
        { positionLy: { x: 10, y: -5, z: 20 } },
        { positionLy: { x: 14, y: -3, z: 25 } },
      ],
      {
        generationId: 'fixture:adapter-crossfade-load-v1',
        pyramidVersion: 'fixture-pyramid-v1',
        level: 2,
        cellSizeLy: 100,
        cellOriginLy: { x: -1_000, y: -1_000, z: -1_000 },
        boundsLy: {
          min: { x: -1_000, y: -1_000, z: -1_000 },
          max: { x: 1_000, y: 1_000, z: 1_000 },
        },
        coverageAsOf: '2026-09-13T00:00:00Z',
      },
    );
    const midBandCamera: GalaxySceneContract['camera'] = {
      focusLy: { x: 0, y: 0, z: 0 },
      distanceLy: (DENSITY_CROSSFADE_NEAR_LY + DENSITY_CROSSFADE_FAR_LY) / 2,
      bearingRad: 0,
      pitchRad: 0.5,
      projection: 'perspective',
      revision: 1,
    };
    const sceneContract: GalaxySceneContract = {
      kind: 'galaxy',
      revision: 1,
      camera: midBandCamera,
      selection: [],
      contributions: [createCatalogueDensityContribution(payload, 1)],
    };

    const engine = new NullEngine();
    const session = createBabylonSession(engine, 'WEBGL2');
    const events = vi.fn();

    expect(
      session.execute?.({ type: 'LOAD_SCENE', scene: sceneContract }, events),
    ).toEqual({ status: 'executed' });

    const scene = engine.scenes[engine.scenes.length - 1]!;
    const densityMesh = scene.getMeshByName('catalogue-density-cells');
    const starMesh = scene.getMeshByName('finder-system-instances');

    expect(densityMesh?.material?.alpha).toBeCloseTo(0.45, 5);
    expect(starMesh?.material?.alpha).toBeCloseTo(0.5, 5);
    const glow = scene.effectLayers.find(
      (layer) => layer.name === 'galaxy-emissive-glow',
    );
    expect(glow).toBeDefined();
    expect(glow!.hasMesh(densityMesh!)).toBe(false);

    session.dispose();
    engine.dispose();
  });

  it('applies the density cross-fade immediately on PATCH_CONTRIBUTION, without waiting for a camera move', () => {
    const midBandCamera: GalaxySceneContract['camera'] = {
      focusLy: { x: 0, y: 0, z: 0 },
      distanceLy: (DENSITY_CROSSFADE_NEAR_LY + DENSITY_CROSSFADE_FAR_LY) / 2,
      bearingRad: 0,
      pitchRad: 0.5,
      projection: 'perspective',
      revision: 1,
    };
    const sceneContract: GalaxySceneContract = {
      kind: 'galaxy',
      revision: 1,
      camera: midBandCamera,
      selection: [],
      contributions: [],
    };

    const engine = new NullEngine();
    const session = createBabylonSession(engine, 'WEBGL2');
    const events = vi.fn();

    expect(
      session.execute?.({ type: 'LOAD_SCENE', scene: sceneContract }, events),
    ).toEqual({ status: 'executed' });

    const payload = buildFixtureCatalogueDensity(
      [
        { positionLy: { x: 10, y: -5, z: 20 } },
        { positionLy: { x: 14, y: -3, z: 25 } },
      ],
      {
        generationId: 'fixture:adapter-crossfade-patch-v1',
        pyramidVersion: 'fixture-pyramid-v1',
        level: 2,
        cellSizeLy: 100,
        cellOriginLy: { x: -1_000, y: -1_000, z: -1_000 },
        boundsLy: {
          min: { x: -1_000, y: -1_000, z: -1_000 },
          max: { x: 1_000, y: 1_000, z: 1_000 },
        },
        coverageAsOf: '2026-09-13T00:00:00Z',
      },
    );
    const contribution = createCatalogueDensityContribution(payload, 2);

    expect(
      session.execute?.({ type: 'PATCH_CONTRIBUTION', contribution }, events),
    ).toEqual({ status: 'executed' });

    const scene = engine.scenes[engine.scenes.length - 1]!;
    const densityMesh = scene.getMeshByName('catalogue-density-cells');
    const starMesh = scene.getMeshByName('finder-system-instances');

    expect(densityMesh?.material?.alpha).toBeCloseTo(0.45, 5);
    expect(starMesh?.material?.alpha).toBeCloseTo(0.5, 5);
    // A refreshed density layer must stay out of the glow layer as well.
    const glow = scene.effectLayers.find(
      (layer) => layer.name === 'galaxy-emissive-glow',
    );
    expect(glow).toBeDefined();
    expect(glow!.hasMesh(densityMesh!)).toBe(false);

    session.dispose();
    engine.dispose();
  });

  it('keeps finder search-result markers opaque through the density cross-fade at wide zoom', () => {
    const payload = buildFixtureCatalogueDensity(
      [
        { positionLy: { x: 10, y: -5, z: 20 } },
        { positionLy: { x: 14, y: -3, z: 25 } },
      ],
      {
        generationId: 'fixture:adapter-finder-preserve-v1',
        pyramidVersion: 'fixture-pyramid-v1',
        level: 2,
        cellSizeLy: 100,
        cellOriginLy: { x: -1_000, y: -1_000, z: -1_000 },
        boundsLy: {
          min: { x: -1_000, y: -1_000, z: -1_000 },
          max: { x: 1_000, y: 1_000, z: 1_000 },
        },
        coverageAsOf: '2026-09-13T00:00:00Z',
      },
    );
    const baseCamera: GalaxySceneContract['camera'] = {
      focusLy: { x: 0, y: 0, z: 0 },
      distanceLy: 2_500,
      bearingRad: 0,
      pitchRad: 0.5,
      projection: 'perspective',
      revision: 1,
    };
    const sceneContract: GalaxySceneContract = {
      kind: 'galaxy',
      revision: 1,
      camera: baseCamera,
      selection: [],
      contributions: [
        createCatalogueDensityContribution(payload, 1),
        {
          id: 'finder-results',
          owner: 'FINDER',
          revision: 1,
          layers: [
            {
              id: 'finder-systems',
              version: 1,
              representation: 'AUTHORITATIVE',
              targetCount: 1,
              truncated: false,
              payload: {
                systems: [
                  {
                    systemId64: '1',
                    name: 'Sol',
                    positionLy: { x: 0, y: 0, z: 0 },
                  },
                ],
              },
            },
          ],
        },
      ],
    };

    const engine = new NullEngine();
    const session = createBabylonSession(engine, 'WEBGL2');
    const events = vi.fn();

    expect(
      session.execute?.({ type: 'LOAD_SCENE', scene: sceneContract }, events),
    ).toEqual({ status: 'executed' });

    const scene = engine.scenes[engine.scenes.length - 1]!;
    const densityMesh = scene.getMeshByName('catalogue-density-cells');
    const starMesh = scene.getMeshByName('finder-result-instances');

    // Zoom fully out into the density-dominant band.
    expect(
      session.execute?.(
        {
          type: 'SET_CAMERA',
          camera: {
            ...baseCamera,
            distanceLy: DENSITY_CROSSFADE_FAR_LY + 1,
            revision: 2,
          },
        },
        events,
      ),
    ).toEqual({ status: 'executed' });

    // Density presents fully, but finder markers must NOT fade with catalogue
    // stars — they stay opaque so search results never vanish at wide zoom.
    expect(densityMesh?.material?.alpha).toBeCloseTo(0.9, 5);
    expect(starMesh?.material?.alpha).toBeCloseTo(1, 5);

    session.dispose();
    engine.dispose();
  });

  it.each([
    [(DENSITY_CROSSFADE_NEAR_LY + DENSITY_CROSSFADE_FAR_LY) / 2, 0.5],
    [DENSITY_CROSSFADE_FAR_LY + 1, 0],
  ])(
    'keeps catalogue alpha at %s ly independent of Finder rows and their accent rings',
    (distanceLy, catalogueAlpha) => {
      const engine = new NullEngine();
      const session = createBabylonSession(engine, 'WEBGL2');
      const events = vi.fn();

      for (const [index, finder] of [[], [batchFinderPoint], []].entries()) {
        const contract = {
          ...densityBatchScene(distanceLy, [batchCataloguePoint], finder),
          revision: index + 1,
        };
        expect(
          session.execute?.({ type: 'LOAD_SCENE', scene: contract }, events),
        ).toEqual({ status: 'executed' });
        const scene = engine.scenes.at(-1)!;
        const catalogueMesh = scene.getMeshByName('finder-system-instances');
        const finderMesh = scene.getMeshByName('finder-result-instances');
        expect(catalogueMesh?.material).not.toBe(finderMesh?.material);
        expect(catalogueMesh?.material?.alpha).toBeCloseTo(catalogueAlpha, 5);
        expect(finderMesh?.material?.alpha).toBe(1);
        expect(catalogueMesh?.isPickable).toBe(catalogueAlpha > 0);
        expect(finderMesh?.isPickable).toBe(true);
        expect(finderMesh?.isEnabled()).toBe(finder.length > 0);
        expect(
          scene.getMeshByName('stellar-icon-ring-42')?.material?.alpha,
        ).toBeCloseTo(catalogueAlpha, 5);
        if (finder.length) {
          expect(
            scene.getMeshByName('stellar-icon-ring-9007199254740993')?.material
              ?.alpha,
          ).toBe(1);
          expect(finderMesh?.metadata).toMatchObject({
            spatialLayer: { id: 'finder-systems', batch: 'finder' },
          });
        }
        expect(catalogueMesh?.metadata).toMatchObject({
          spatialLayer: { id: 'catalogue-systems', batch: 'catalogue' },
        });
        expect(events).toHaveBeenCalledWith({
          type: 'CAMERA_CHANGED',
          camera: contract.camera,
        });
      }

      session.dispose();
    },
  );

  it('keeps the density layer out of bloom after the only glow mesh is deselected', () => {
    // A plain G star has no accent ring and there is no commander history, so
    // the selection marker is the only glow mesh this scene starts with.
    const plainStar: GalaxySystemPoint = {
      systemId64: '77',
      name: 'Plain G star',
      positionLy: { x: 30, y: 0, z: 0 },
      primaryStar: { type: 'G' },
    };
    const base = densityBatchScene(
      DENSITY_CROSSFADE_FAR_LY + 1,
      [plainStar],
      [],
    );
    const engine = new NullEngine();
    const session = createBabylonSession(engine, 'WEBGL2');
    const events = vi.fn();
    session.execute?.(
      {
        type: 'LOAD_SCENE',
        scene: {
          ...base,
          selection: [{ kind: 'system', systemId64: plainStar.systemId64 }],
        },
      },
      events,
    );
    const scene = engine.scenes.at(-1)!;
    expect(scene.getMeshByName('selected-system-marker')).not.toBeNull();
    expect(
      scene.getMeshByName(`stellar-icon-ring-${plainStar.systemId64}`),
    ).toBeNull();
    const glow = scene.effectLayers.find(
      (layer) => layer.name === 'galaxy-emissive-glow',
    )!;
    const densityMesh = scene.getMeshByName('catalogue-density-cells')!;
    expect(glow.hasMesh(densityMesh)).toBe(false);

    session.execute?.(
      { type: 'LOAD_SCENE', scene: { ...base, revision: 2, selection: [] } },
      events,
    );
    // Deselection takes the in-place path: same scene, marker removed.
    expect(engine.scenes.at(-1)).toBe(scene);
    expect(scene.getMeshByName('selected-system-marker')).toBeNull();
    expect(glow.hasMesh(densityMesh)).toBe(false);

    session.dispose();
    engine.dispose();
  });

  it('moves a selected catalogue star into the opaque batch after zoom without changing unrelated stars', () => {
    const selectedPoint = {
      ...batchFinderPoint,
      positionLy: { x: 30, y: 0, z: 0 },
    };
    const base = densityBatchScene(
      DENSITY_CROSSFADE_NEAR_LY - 1,
      [batchCataloguePoint, selectedPoint],
      [],
    );
    const initial = {
      ...base,
      camera: { ...base.camera, focusLy: selectedPoint.positionLy },
    };
    const engine = new NullEngine();
    const session = createBabylonSession(engine, 'WEBGL2');
    const events = vi.fn();
    session.execute?.({ type: 'LOAD_SCENE', scene: initial }, events);
    const scene = engine.scenes.at(-1)!;
    const starGeometry = [
      'finder-system-instances',
      'finder-result-instances',
    ].map((name) => {
      const mesh = scene.getMeshByName(name)!;
      return {
        mesh,
        vertices: Float32Array.from(mesh.getVerticesData('position') ?? []),
        halfHeight: mesh.getBoundingInfo().boundingBox.extendSize.y,
      };
    });
    const farCamera = {
      ...initial.camera,
      distanceLy: DENSITY_CROSSFADE_FAR_LY + 1,
      revision: 2,
    };
    expect(
      session.execute?.({ type: 'SET_CAMERA', camera: farCamera }, events),
    ).toEqual({ status: 'executed' });
    const scale =
      galaxyStarMarkerSizeLy(farCamera.distanceLy) /
      galaxyStarMarkerSizeLy(initial.camera.distanceLy);
    for (const { mesh, vertices, halfHeight } of starGeometry) {
      expect(Array.from(mesh.getVerticesData('position') ?? [])).toEqual(
        Array.from(vertices.map((value) => value * scale)),
      );
      expect(mesh.getBoundingInfo().boundingBox.extendSize.y).toBeCloseTo(
        halfHeight * scale,
        4,
      );
    }

    for (const [index, selection] of [
      [{ kind: 'system' as const, systemId64: selectedPoint.systemId64 }],
      [],
    ].entries()) {
      expect(
        session.execute?.(
          {
            type: 'LOAD_SCENE',
            scene: {
              ...initial,
              camera: farCamera,
              revision: index + 2,
              selection,
            },
          },
          events,
        ),
      ).toEqual({ status: 'executed' });
      expect(engine.scenes.at(-1)).toBe(scene);
      expect(
        scene.getMeshByName('finder-system-instances')?.material?.alpha,
      ).toBe(0);
      expect(scene.getMeshByName('stellar-icon-ring-42')?.material?.alpha).toBe(
        0,
      );
      expect(
        scene.getMeshByName('stellar-icon-ring-9007199254740993')?.material
          ?.alpha,
      ).toBe(selection.length ? 1 : 0);
      const finderMesh = scene.getMeshByName('finder-result-instances');
      expect(finderMesh?.isEnabled()).toBe(selection.length > 0);
      expect(finderMesh?.material?.alpha).toBe(1);
      expect(finderMesh?.isPickable).toBe(true);
      expect(scene.getMeshByName('selected-system-marker') !== null).toBe(
        selection.length > 0,
      );
      expect(scene.metadata.spatialScene.renderedLayers).toContainEqual({
        contributionId: 'catalogue-viewport-stars',
        contributionRevision: 1,
        layerId: 'catalogue-systems',
        layerVersion: 1,
        representation: 'AUTHORITATIVE',
        acceptedTargetCount: 2,
        renderedTargetCount: 2,
        renderedBatchCounts: {
          catalogue: 2 - selection.length,
          finder: selection.length,
        },
      });
      scene.updateTransformMatrix(true);
      for (const mesh of scene.meshes) mesh.computeWorldMatrix(true);
      if (selection.length) {
        // Replacing thin-instance buffers must retain the geometry and bounds
        // already scaled for this camera, so exact rays still hit the star.
        expect(
          finderMesh?.getBoundingInfo().boundingBox.center.asArray(),
        ).toEqual([30, 0, 0]);
        expect(
          finderMesh?.getBoundingInfo().boundingBox.extendSize.y,
        ).toBeCloseTo(starGeometry[0]!.halfHeight * scale, 4);
        session.execute?.(
          {
            type: 'HOVER',
            screenX: engine.getRenderWidth() / 2,
            screenY: engine.getRenderHeight() / 2,
          },
          events,
        );
        expect(events).toHaveBeenLastCalledWith({
          type: 'TARGET_HOVERED',
          target: {
            kind: 'system',
            systemId64: selectedPoint.systemId64,
            name: selectedPoint.name,
          },
          screenX: engine.getRenderWidth() / 2,
          screenY: engine.getRenderHeight() / 2,
        });
      }
      session.execute?.(
        {
          type: 'PICK',
          screenX: engine.getRenderWidth() / 2,
          screenY: engine.getRenderHeight() / 2,
        },
        events,
      );
      expect(events).toHaveBeenLastCalledWith({
        type: 'TARGET_PICKED',
        target: selection.length
          ? { kind: 'system', systemId64: selectedPoint.systemId64 }
          : undefined,
      });
    }

    session.dispose();
  });

  it.each([
    ['exact', DENSITY_CROSSFADE_NEAR_LY - 1],
    ['exact', DENSITY_CROSSFADE_FAR_LY + 1],
    ['fallback', DENSITY_CROSSFADE_NEAR_LY - 1],
    ['fallback', DENSITY_CROSSFADE_FAR_LY + 1],
  ] as const)(
    'prioritizes Finder identity on a %s picking tie at %s ly and excludes faded catalogue stars',
    (path, distanceLy) => {
      const engine = new NullEngine({
        renderWidth: 640,
        renderHeight: 360,
        textureSize: 512,
        deterministicLockstep: true,
        lockstepMaxSteps: 1,
      });
      const session = createBabylonSession(engine, 'WEBGL2');
      const events = vi.fn();
      const contract = densityBatchScene(distanceLy);
      session.execute?.({ type: 'LOAD_SCENE', scene: contract }, events);
      const scene = engine.scenes.at(-1)!;
      scene.updateTransformMatrix(true);
      for (const mesh of scene.meshes) mesh.computeWorldMatrix(true);
      const pick = vi.spyOn(scene, 'pick');
      if (path === 'fallback') pick.mockReturnValue(new PickingInfo());
      // HOVER only performs exact ray picks; PICK also enables the projected
      // fallback, which the ray miss above isolates deliberately.
      session.execute?.(
        {
          type: path === 'exact' ? 'HOVER' : 'PICK',
          screenX: 320,
          screenY: 180,
        },
        events,
      );
      expect(events).toHaveBeenLastCalledWith(
        path === 'exact'
          ? {
              type: 'TARGET_HOVERED',
              target: {
                kind: 'system',
                systemId64: batchFinderPoint.systemId64,
                name: batchFinderPoint.name,
              },
              screenX: 320,
              screenY: 180,
            }
          : {
              type: 'TARGET_PICKED',
              target: {
                kind: 'system',
                systemId64: batchFinderPoint.systemId64,
              },
            },
      );
      pick.mockRestore();

      // Move the Finder target well outside the click tolerance: catalogue
      // stars can be picked at close zoom, but cannot intercept at wide zoom.
      session.execute?.(
        {
          type: 'LOAD_SCENE',
          scene: {
            ...densityBatchScene(
              distanceLy,
              [batchCataloguePoint],
              [
                {
                  ...batchFinderPoint,
                  positionLy: { x: distanceLy, y: 0, z: 0 },
                },
              ],
            ),
            revision: 2,
          },
        },
        events,
      );
      const replacement = engine.scenes.at(-1)!;
      replacement.updateTransformMatrix(true);
      for (const mesh of replacement.meshes) mesh.computeWorldMatrix(true);
      const replacementPick = vi.spyOn(replacement, 'pick');
      if (path === 'fallback')
        replacementPick.mockReturnValue(new PickingInfo());
      session.execute?.({ type: 'PICK', screenX: 320, screenY: 180 }, events);
      expect(events).toHaveBeenLastCalledWith({
        type: 'TARGET_PICKED',
        target:
          distanceLy < DENSITY_CROSSFADE_FAR_LY
            ? { kind: 'system', systemId64: batchCataloguePoint.systemId64 }
            : undefined,
      });
      replacementPick.mockRestore();
      session.dispose();
    },
  );

  it('preserves layer receipt fields and shared id64 identity while adding batch counts', () => {
    const contract = densityBatchScene(
      DENSITY_CROSSFADE_FAR_LY + 1,
      [batchCataloguePoint, { ...batchFinderPoint, name: 'Catalogue copy' }],
      [batchFinderPoint],
    );
    const engine = new NullEngine();
    const product = createBabylonGalaxyScene(engine, contract);

    expect(product.points.map(({ systemId64 }) => systemId64)).toEqual([
      batchCataloguePoint.systemId64,
      batchFinderPoint.systemId64,
    ]);
    expect(product.catalogueStars.points).toEqual([batchCataloguePoint]);
    expect(product.finderStars.points).toEqual([batchFinderPoint]);
    expect(product.catalogueStars.mesh.thinInstanceCount).toBe(1);
    expect(product.finderStars.mesh.thinInstanceCount).toBe(1);
    expect(product.renderedLayers.slice(1)).toEqual([
      {
        contributionId: 'catalogue-viewport-stars',
        contributionRevision: 1,
        layerId: 'catalogue-systems',
        layerVersion: 1,
        representation: 'AUTHORITATIVE',
        acceptedTargetCount: 2,
        renderedTargetCount: 2,
        renderedBatchCounts: { catalogue: 1, finder: 1 },
      },
      {
        contributionId: 'finder-results',
        contributionRevision: 1,
        layerId: 'finder-systems',
        layerVersion: 1,
        representation: 'AUTHORITATIVE',
        acceptedTargetCount: 1,
        renderedTargetCount: 1,
        renderedBatchCounts: { catalogue: 0, finder: 1 },
      },
    ]);
    expect(product.scene.metadata).toMatchObject({
      spatialScene: { renderedLayers: product.renderedLayers },
    });

    product.scene.dispose();
    engine.dispose();
  });

  it('fades catalogue-only stars, their accent rings, and their pickability into the density field', () => {
    const payload = buildFixtureCatalogueDensity(
      [
        { positionLy: { x: 10, y: -5, z: 20 } },
        { positionLy: { x: 14, y: -3, z: 25 } },
      ],
      {
        generationId: 'fixture:adapter-catalogue-fade-v1',
        pyramidVersion: 'fixture-pyramid-v1',
        level: 2,
        cellSizeLy: 100,
        cellOriginLy: { x: -1_000, y: -1_000, z: -1_000 },
        boundsLy: {
          min: { x: -1_000, y: -1_000, z: -1_000 },
          max: { x: 1_000, y: 1_000, z: 1_000 },
        },
        coverageAsOf: '2026-09-13T00:00:00Z',
      },
    );
    const baseCamera: GalaxySceneContract['camera'] = {
      focusLy: { x: 0, y: 0, z: 0 },
      distanceLy: 2_500,
      bearingRad: 0,
      pitchRad: 0.5,
      projection: 'perspective',
      revision: 1,
    };
    const sceneContract: GalaxySceneContract = {
      kind: 'galaxy',
      revision: 1,
      camera: baseCamera,
      selection: [],
      contributions: [
        createCatalogueDensityContribution(payload, 1),
        {
          id: 'catalogue-viewport-stars',
          owner: 'CATALOGUE',
          revision: 1,
          layers: [
            {
              id: 'catalogue-systems',
              version: 1,
              representation: 'AUTHORITATIVE',
              targetCount: 1,
              truncated: false,
              payload: {
                systems: [
                  {
                    systemId64: '9',
                    name: 'Void',
                    positionLy: { x: 5, y: 0, z: 5 },
                    primaryStar: { type: 'Black Hole' },
                  },
                ],
              },
            },
          ],
        },
      ],
    };

    const engine = new NullEngine();
    const session = createBabylonSession(engine, 'WEBGL2');
    const events = vi.fn();

    expect(
      session.execute?.({ type: 'LOAD_SCENE', scene: sceneContract }, events),
    ).toEqual({ status: 'executed' });

    const scene = engine.scenes[engine.scenes.length - 1]!;
    const starMesh = scene.getMeshByName('finder-system-instances');
    const accentRing = scene.getMeshByName('stellar-icon-ring-9');
    expect(accentRing).not.toBeNull();

    const setDistance = (distanceLy: number, revision: number): void => {
      expect(
        session.execute?.(
          {
            type: 'SET_CAMERA',
            camera: { ...baseCamera, distanceLy, revision },
          },
          events,
        ),
      ).toEqual({ status: 'executed' });
    };

    // Star-dominant end: catalogue stars, accent rings and picking are live.
    setDistance(DENSITY_CROSSFADE_NEAR_LY - 1, 2);
    expect(starMesh?.material?.alpha).toBeCloseTo(1, 5);
    expect(accentRing?.material?.alpha).toBeCloseTo(1, 5);
    expect(starMesh?.isPickable).toBe(true);
    expect(
      (starMesh as unknown as { thinInstanceEnablePicking: boolean })
        .thinInstanceEnablePicking,
    ).toBe(true);

    // Density-dominant end: catalogue stars and their rings fade to nothing and
    // stop capturing picks so they cannot block region selection.
    setDistance(DENSITY_CROSSFADE_FAR_LY + 1, 3);
    expect(starMesh?.material?.alpha).toBeCloseTo(0, 5);
    expect(accentRing?.material?.alpha).toBeCloseTo(0, 5);
    expect(starMesh?.isPickable).toBe(false);
    expect(
      (starMesh as unknown as { thinInstanceEnablePicking: boolean })
        .thinInstanceEnablePicking,
    ).toBe(false);

    session.dispose();
    engine.dispose();
  });

  it('scales only star presentation radius as camera distance changes', () => {
    expect(galaxyStarMarkerSizeLy(100)).toBeCloseTo(0.6);
    expect(galaxyStarMarkerSizeLy(1_000)).toBeCloseTo(6);
    expect(() => galaxyStarMarkerSizeLy(0)).toThrow(/positive/u);
  });

  it('renders catalogue star classes as distinct icon colours and scales without moving them', () => {
    const engine = new NullEngine();
    const sceneContract: GalaxySceneContract = {
      kind: 'galaxy',
      revision: 16,
      camera: {
        focusLy: { x: 0, y: 0, z: 0 },
        distanceLy: 800,
        bearingRad: 0,
        pitchRad: 0.5,
        projection: 'perspective',
        revision: 16,
      },
      selection: [],
      contributions: [
        {
          id: 'finder-results',
          owner: 'FINDER',
          revision: 16,
          layers: [
            {
              id: 'finder-systems',
              version: 1,
              representation: 'AUTHORITATIVE',
              targetCount: 3,
              truncated: false,
              payload: {
                systems: [
                  {
                    systemId64: '1',
                    name: 'Golden One',
                    positionLy: { x: 11, y: -2, z: 35 },
                    primaryStar: { type: 'G', subtype: '2 V' },
                  },
                  {
                    systemId64: '2',
                    name: 'Blue Giant',
                    positionLy: { x: -14, y: 8, z: 120 },
                    primaryStar: { type: 'B', subtype: 'Ia Supergiant' },
                  },
                  {
                    systemId64: '3',
                    name: 'Dark Ring',
                    positionLy: { x: 70, y: 0, z: -9 },
                    primaryStar: { type: 'Black Hole' },
                  },
                ],
              },
            },
          ],
        },
      ],
    };

    const product = createBabylonGalaxyScene(engine, sceneContract);
    const matrixBuffer = product.finderStars.instanceMatrices;
    const colourBuffer = product.finderStars.instanceColours;

    expect(
      product.finderStars.stellarPresentations.map(({ glyph }) => glyph),
    ).toEqual(['main-sequence-g', 'giant-blue', 'black-hole']);
    expect(Array.from(colourBuffer.slice(0, 4))).not.toEqual(
      Array.from(colourBuffer.slice(4, 8)),
    );
    expect(product.stellarAccentMeshes).toHaveLength(1);
    expect(product.stellarAccentMeshes[0]?.metadata).toMatchObject({
      spatialLayer: { stellarGlyph: 'black-hole', presentationOnly: true },
    });
    [
      [11, -2, 35],
      [-14, 8, 120],
      [70, 0, -9],
    ].forEach((expected, index) => {
      const matrix = Matrix.FromArray(matrixBuffer, index * 16);
      expect(matrix.getTranslation().asArray()).toEqual(expected);
    });

    product.scene.dispose();
    engine.dispose();
  });

  it('renders journal visits in a separate non-pickable commander heatmap', () => {
    const engine = new NullEngine();
    const history = createCommanderHistoryContribution(
      'density',
      [
        {
          kind: 'density',
          x: 100,
          y: -50,
          z: 300,
          visitCount: 2,
          firstVisitedAt: '2025-01-01T00:00:00Z',
          lastVisitedAt: '2025-02-01T00:00:00Z',
          cellSizeLy: 50,
        },
        {
          kind: 'density',
          x: 150,
          y: -50,
          z: 350,
          visitCount: 18,
          firstVisitedAt: '2025-01-01T00:00:00Z',
          lastVisitedAt: '2026-09-14T00:00:00Z',
          cellSizeLy: 50,
        },
      ],
      22,
      false,
    );
    const sceneContract: GalaxySceneContract = {
      kind: 'galaxy',
      revision: 22,
      camera: {
        focusLy: { x: 100, y: -50, z: 300 },
        distanceLy: 500,
        bearingRad: 0,
        pitchRad: 0.6,
        projection: 'perspective',
        revision: 22,
      },
      selection: [],
      contributions: [history],
    };

    const product = createBabylonGalaxyScene(engine, sceneContract);

    expect(product.commanderHistoryMesh?.thinInstanceCount).toBe(2);
    expect(product.commanderHistoryMesh?.isPickable).toBe(false);
    expect(product.commanderHistoryMesh?.metadata).toMatchObject({
      spatialLayer: {
        id: 'commander-history-heatmap',
        owner: 'COMMANDER_HISTORY',
        source: 'journal-log',
        catalogueDensity: false,
      },
    });
    expect(product.renderedLayers).toContainEqual({
      contributionId: 'commander-history',
      contributionRevision: 22,
      layerId: 'commander-history-heatmap',
      layerVersion: 1,
      representation: 'DERIVED',
      acceptedTargetCount: 2,
      renderedTargetCount: 2,
    });
    expect(commanderHistoryHeatColour(2, 18)).not.toEqual(
      commanderHistoryHeatColour(18, 18),
    );

    product.scene.dispose();
    engine.dispose();
  });

  it('renders attributed nebula landmarks at their authoritative coordinates with schematic extents', () => {
    const payload: GalaxyNebulaePayload = {
      datasetId: 'fixture-nebulae',
      sourceUrl: 'https://edastro.com/mapcharts/files/nebulae-coordinates.csv',
      catalogueUrl: 'https://edastro.com/mapcharts/files.html',
      rightsNotice: 'Copyright © CMDR Orvidius — All Rights Reserved',
      usageBasis:
        'Purpose-published automation-ready CSV; non-commercial use accepted by the ED-Finder owner',
      attribution: 'EDAstro / CMDR Orvidius',
      sourceLastModified: 'Sun, 13 Sep 2026 15:00:58 GMT',
      sourceByteCount: 123,
      sourceSha256: 'a'.repeat(64),
      nebulae: [
        {
          id: 'GMP:1',
          source: 'GMP',
          sourceId: '1',
          name: 'Purple Cloud',
          systemName: 'Cloud AA-A h1',
          regionName: null,
          regionId: 18,
          kind: 'nebula',
          positionLy: {
            value: { x: 125, y: -34, z: 912 },
            representation: 'AUTHORITATIVE',
          },
          poiUrl: null,
        },
      ],
    };
    const engine = new NullEngine();
    const sceneContract: GalaxySceneContract = {
      kind: 'galaxy',
      revision: 17,
      camera: {
        focusLy: { x: 0, y: 0, z: 0 },
        distanceLy: 10_000,
        bearingRad: 0,
        pitchRad: 0.5,
        projection: 'perspective',
        revision: 17,
      },
      selection: [],
      contributions: [createGalaxyNebulaeContribution(payload, 17)],
    };

    const product = createBabylonGalaxyScene(engine, sceneContract);
    expect(product.nebulaMesh?.thinInstanceCount).toBe(1);
    expect(product.nebulaMesh?.isPickable).toBe(false);
    expect(product.nebulaMesh?.metadata).toMatchObject({
      spatialLayer: {
        id: 'galaxy-nebulae',
        coordinateRepresentation: 'AUTHORITATIVE',
        radiusMeaning: 'schematic-map-presentation-not-physical-extent',
        rightsNotice: 'Copyright © CMDR Orvidius — All Rights Reserved',
      },
    });
    const matrixData = product.nebulaMesh?.thinInstanceGetWorldMatrices()[0];
    expect(matrixData?.getTranslation().asArray()).toEqual([125, -34, 912]);
    expect(nebulaCloudRadiusLy()).toBe(1_200);
    expect(product.renderedLayers[0]).toMatchObject({
      layerId: 'galaxy-nebulae',
      representation: 'AMBIENT',
      acceptedTargetCount: 1,
      renderedTargetCount: 1,
    });

    product.scene.dispose();
    engine.dispose();
  });

  it('keeps nebula landmarks at system-selection distance while fading their schematic clouds', () => {
    const payload: GalaxyNebulaePayload = {
      datasetId: 'fixture-nebulae',
      sourceUrl: 'https://edastro.com/mapcharts/files/nebulae-coordinates.csv',
      catalogueUrl: 'https://edastro.com/mapcharts/files.html',
      rightsNotice: 'Copyright © CMDR Orvidius — All Rights Reserved',
      usageBasis:
        'Purpose-published automation-ready CSV; non-commercial use accepted by the ED-Finder owner',
      attribution: 'EDAstro / CMDR Orvidius',
      sourceLastModified: 'Sun, 13 Sep 2026 15:00:58 GMT',
      sourceByteCount: 123,
      sourceSha256: 'a'.repeat(64),
      nebulae: [
        {
          id: 'GMP:1',
          source: 'GMP',
          sourceId: '1',
          name: 'Purple Cloud',
          systemName: 'Cloud AA-A h1',
          regionName: null,
          regionId: 18,
          kind: 'nebula',
          positionLy: {
            value: { x: 125, y: -34, z: 912 },
            representation: 'AUTHORITATIVE',
          },
          poiUrl: null,
        },
      ],
    };
    const buildContractAt = (distanceLy: number, revision: number) =>
      ({
        kind: 'galaxy',
        revision,
        camera: {
          focusLy: { x: 0, y: 0, z: 0 },
          distanceLy,
          bearingRad: 0,
          pitchRad: 0.5,
          projection: 'perspective',
          revision,
        },
        selection: [],
        contributions: [createGalaxyNebulaeContribution(payload, revision)],
      }) satisfies GalaxySceneContract;

    const closeEngine = new NullEngine();
    // ~400 ly matches ExploreWorkspace's system-selection camera target; the
    // decorative clouds fade out here while their catalogue markers persist.
    const closeProduct = createBabylonGalaxyScene(
      closeEngine,
      buildContractAt(400, 1),
    );
    expect(closeProduct.nebulaMesh?.isEnabled()).toBe(true);
    expect(closeProduct.nebulaMesh?.visibility).toBe(1);
    expect(closeProduct.nebulaCloudMesh?.isEnabled()).toBe(false);
    expect(closeProduct.nebulaCloudMesh?.visibility).toBe(0);
    expect(closeProduct.renderedLayers[0]?.renderedTargetCount).toBe(1);
    closeProduct.scene.dispose();
    closeEngine.dispose();

    const farEngine = new NullEngine();
    // Galaxy-overview scale shows both the marker and its schematic cloud.
    const farProduct = createBabylonGalaxyScene(
      farEngine,
      buildContractAt(118_000, 2),
    );
    expect(farProduct.nebulaMesh?.isEnabled()).toBe(true);
    expect(farProduct.nebulaMesh?.visibility).toBe(1);
    expect(farProduct.nebulaCloudMesh?.isEnabled()).toBe(true);
    expect(farProduct.nebulaCloudMesh?.visibility).toBe(1);
    expect(farProduct.renderedLayers[0]?.renderedTargetCount).toBe(1);
    farProduct.scene.dispose();
    farEngine.dispose();
  });

  it.each([
    ['WEBGPU', 'webgpu-device-lost', 'webgpu-device-restored'],
    ['WEBGL2', 'webgl2-context-lost', 'webgl2-context-restored'],
  ] as const)(
    'maps %s engine resource observables and removes both observers once',
    (backend, lostDetail, restoredDetail) => {
      let notifyLost: () => void = () => undefined;
      let notifyRestored: () => void = () => undefined;
      const removeLost = vi.fn();
      const removeRestored = vi.fn();
      const engine = {
        onContextLostObservable: {
          add: vi.fn((listener: () => void) => {
            notifyLost = listener;
            return { remove: removeLost };
          }),
        },
        onContextRestoredObservable: {
          add: vi.fn((listener: () => void) => {
            notifyRestored = listener;
            return { remove: removeRestored };
          }),
        },
      } as unknown as Parameters<typeof subscribeToBabylonResourceEvents>[0];
      const listener = vi.fn();

      const unsubscribe = subscribeToBabylonResourceEvents(
        engine,
        backend,
        listener,
      );
      notifyLost();
      notifyRestored();

      expect(listener.mock.calls.map(([event]) => event)).toEqual([
        { state: 'lost', detail: lostDetail },
        { state: 'recovered', detail: restoredDetail },
      ]);

      unsubscribe();
      unsubscribe();
      expect(removeLost).toHaveBeenCalledOnce();
      expect(removeRestored).toHaveBeenCalledOnce();
    },
  );

  it('uses a fresh canvas for WebGL2 after WebGPU initialization fails', async () => {
    const original = document.createElement('canvas');
    const replacement = document.createElement('canvas');
    const webGl2 = createSession('WEBGL2');
    const dependencies: BabylonRuntimeDependencies = {
      createWebGpu: vi
        .fn()
        .mockRejectedValue(new Error('initialization failed')),
      createWebGl2: vi.fn().mockReturnValue(webGl2),
      replaceCanvas: vi.fn().mockReturnValue(replacement),
    };
    const runtime = createBabylonSpatialRuntime(
      original,
      vi.fn(),
      dependencies,
    );

    await expect(runtime.start()).resolves.toEqual({
      state: 'ready',
      backend: 'WEBGL2',
    });

    expect(dependencies.replaceCanvas).toHaveBeenCalledWith(original);
    expect(dependencies.createWebGl2).toHaveBeenCalledWith(replacement);
  });

  it('keeps the original canvas when WebGPU is simply unsupported', async () => {
    const original = document.createElement('canvas');
    const webGl2 = createSession('WEBGL2');
    const dependencies: BabylonRuntimeDependencies = {
      createWebGpu: vi.fn().mockResolvedValue(null),
      createWebGl2: vi.fn().mockReturnValue(webGl2),
      replaceCanvas: vi.fn(),
    };
    const runtime = createBabylonSpatialRuntime(
      original,
      vi.fn(),
      dependencies,
    );

    await runtime.start();

    expect(dependencies.replaceCanvas).not.toHaveBeenCalled();
    expect(dependencies.createWebGl2).toHaveBeenCalledWith(original);
  });
});
