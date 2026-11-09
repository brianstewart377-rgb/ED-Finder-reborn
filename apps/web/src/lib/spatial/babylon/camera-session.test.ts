import { afterEach, describe, expect, it, vi } from 'vitest';
import { Camera } from '@babylonjs/core/Cameras/camera.js';
import { FreeCamera } from '@babylonjs/core/Cameras/freeCamera.js';
import { NullEngine } from '@babylonjs/core/Engines/nullEngine.js';
import { Vector3 } from '@babylonjs/core/Maths/math.vector.js';
import type { AbstractMesh } from '@babylonjs/core/Meshes/abstractMesh.js';
import { Mesh } from '@babylonjs/core/Meshes/mesh.js';
import type { Scene } from '@babylonjs/core/scene.js';

import { buildAuthoritativeGalaxyRegionsAsset } from '../../../../vite.galaxy-regions';
import type {
  CameraState,
  GalaxySceneContract,
  RuntimeEvent,
  SpatialTarget,
  Vec3Ly,
} from '../contracts';
import {
  GALAXY_CAMERA_FOV_RAD,
  GALAXY_LOCAL_VIEW_DISTANCE_LY,
  galaxyCameraPose,
  normalizeGalaxyCamera,
} from '../galaxy-camera';
import { buildFixtureCatalogueDensity } from '../galaxy-density.fixture';
import { createCatalogueDensityContribution } from '../galaxy-density';
import {
  createGalaxyRegionsContribution,
  validateGalaxyRegionsPayload,
} from '../galaxy-regions';
import {
  applyGalaxyCamera,
  createBabylonGalaxyScene,
  createBabylonSession,
} from './adapter';

const initialCamera: CameraState = {
  focusLy: { x: 50, y: 10, z: 50 },
  distanceLy: 1_000,
  bearingRad: 0,
  pitchRad: 0.55,
  projection: 'perspective',
  revision: 1,
};
const destination = {
  systemId64: '9007199254740993',
  name: 'Camera flight fixture',
  positionLy: { x: 800, y: 5, z: 700 },
};
const flightTarget: SpatialTarget = {
  kind: 'system',
  systemId64: destination.systemId64,
};
const fixture: GalaxySceneContract = {
  kind: 'galaxy',
  revision: 1,
  camera: initialCamera,
  selection: [flightTarget],
  contributions: [
    {
      id: 'camera-fixture-results',
      owner: 'FINDER',
      revision: 1,
      layers: [
        {
          id: 'finder-systems',
          version: 1,
          representation: 'AUTHORITATIVE',
          payload: { systems: [destination] },
          targetCount: 1,
          truncated: false,
        },
      ],
    },
  ],
};

const cleanup: Array<() => void> = [];
afterEach(() => {
  for (const dispose of cleanup.splice(0)) dispose();
  vi.restoreAllMocks();
});

function createEngine(): NullEngine {
  return new NullEngine({
    renderWidth: 640,
    renderHeight: 360,
    textureSize: 512,
    deterministicLockstep: true,
    lockstepMaxSteps: 1,
  });
}

function loadSession(contract = fixture) {
  const engine = createEngine();
  let timestamp = 10_000;
  const session = createBabylonSession(engine, 'WEBGL2', () => timestamp);
  cleanup.push(() => session.dispose());
  const events: RuntimeEvent[] = [];
  const emit = (event: RuntimeEvent) => events.push(event);
  expect(
    session.execute?.({ type: 'LOAD_SCENE', scene: contract }, emit),
  ).toEqual({ status: 'executed' });
  expect(engine.scenes).toHaveLength(1);
  const scene = engine.scenes[0]!;
  const camera = scene.activeCamera as FreeCamera;
  // These tests exercise actual camera matrices, meshes and session timing.
  // NullEngine's asynchronous shader readiness is outside this boundary.
  vi.spyOn(scene, 'render').mockImplementation(() => undefined);
  vi.spyOn(scene, 'isReady').mockReturnValue(true);
  return {
    engine,
    session,
    scene,
    camera,
    events,
    emit,
    tick(elapsed: number) {
      timestamp = 10_000 + elapsed;
      return session.render();
    },
  };
}

function expectVector(actual: Vec3Ly, expected: Vec3Ly) {
  expect(actual.x).toBeCloseTo(expected.x, 4);
  expect(actual.y).toBeCloseTo(expected.y, 4);
  expect(actual.z).toBeCloseTo(expected.z, 4);
}

function expectCameraPose(camera: FreeCamera, state: CameraState) {
  const pose = galaxyCameraPose(state);
  expectVector(camera.position, pose.positionLy);
  camera.getViewMatrix(true);
  const world = camera.getWorldMatrix();
  const forward = Vector3.TransformNormal(Vector3.Forward(), world).normalize();
  const up = Vector3.TransformNormal(Vector3.Up(), world).normalize();
  const expectedForward = new Vector3(
    pose.targetLy.x - pose.positionLy.x,
    pose.targetLy.y - pose.positionLy.y,
    pose.targetLy.z - pose.positionLy.z,
  ).normalize();
  expectVector(forward, expectedForward);
  expectVector(up, pose.up);
}

function changedCameras(events: RuntimeEvent[]): CameraState[] {
  return events.flatMap((event) =>
    event.type === 'CAMERA_CHANGED' && 'focusLy' in event.camera
      ? [event.camera]
      : [],
  );
}

function stableSceneMeshes(scene: Scene): readonly AbstractMesh[] {
  return scene.meshes.filter(
    (mesh) => !mesh.name.startsWith('galaxy-reference-grid-'),
  );
}

describe('Babylon Galaxy camera session', () => {
  it('applies SET_CAMERA to the existing camera and meshes with true bearing, pitch and projection', () => {
    const { engine, session, scene, camera, emit, events } = loadSession();
    const meshes = stableSceneMeshes(scene);
    const disposeScene = vi.spyOn(scene, 'dispose');
    const desired: CameraState = {
      ...initialCamera,
      focusLy: { x: -450, y: 20, z: 630 },
      distanceLy: 400,
      bearingRad: 1.2,
      pitchRad: 0.8,
      projection: 'orthographic',
      revision: 2,
    };

    expect(
      session.execute?.({ type: 'SET_CAMERA', camera: desired }, emit),
    ).toEqual({ status: 'executed' });

    expectCameraPose(camera, desired);
    expect(camera.mode).toBe(Camera.ORTHOGRAPHIC_CAMERA);
    const halfHeight = desired.distanceLy * Math.tan(GALAXY_CAMERA_FOV_RAD / 2);
    expect(camera.orthoTop).toBeCloseTo(halfHeight);
    expect(camera.orthoRight).toBeCloseTo((halfHeight * 640) / 360);
    expect(changedCameras(events).at(-1)).toEqual(
      normalizeGalaxyCamera(desired),
    );
    expect(engine.scenes).toEqual([scene]);
    expect(scene.activeCamera).toBe(camera);
    expect(stableSceneMeshes(scene)).toEqual(meshes);
    expect(scene.getMeshByName('galaxy-reference-grid-minor')).not.toBeNull();
    expect(scene.getMeshByName('galaxy-reference-grid-major')).not.toBeNull();
    expect(disposeScene).not.toHaveBeenCalled();
  });

  it('patches system selection in place without rebuilding the Galaxy scene', () => {
    const second = {
      systemId64: '128',
      name: 'Second selection fixture',
      positionLy: { x: -240, y: 12, z: 360 },
    };
    const contribution = {
      ...fixture.contributions[0]!,
      layers: [
        {
          ...fixture.contributions[0]!.layers[0]!,
          payload: { systems: [destination, second] },
          targetCount: 2,
        },
      ],
    };
    const initial: GalaxySceneContract = {
      ...fixture,
      contributions: [contribution],
    };
    const { engine, session, scene, camera, emit, events, tick } =
      loadSession(initial);
    const finderMesh = scene.getMeshByName(
      'finder-result-instances',
    ) as Mesh | null;
    const marker = scene.getMeshByName('selected-system-marker');
    const disposeScene = vi.spyOn(scene, 'dispose');
    expect(marker?.position.asArray()).toEqual([
      destination.positionLy.x,
      destination.positionLy.y,
      destination.positionLy.z,
    ]);
    expect(
      finderMesh
        ?.thinInstanceGetWorldMatrices()
        .map((matrix) => [matrix.m[12], matrix.m[13], matrix.m[14]]),
    ).toEqual([
      [
        destination.positionLy.x,
        destination.positionLy.y,
        destination.positionLy.z,
      ],
      [second.positionLy.x, second.positionLy.y, second.positionLy.z],
    ]);

    expect(
      session.execute?.(
        {
          type: 'LOAD_SCENE',
          scene: {
            ...initial,
            revision: 2,
            selection: [{ kind: 'system', systemId64: second.systemId64 }],
          },
        },
        emit,
      ),
    ).toEqual({ status: 'executed' });

    expect(engine.scenes).toEqual([scene]);
    expect(scene.activeCamera).toBe(camera);
    expect(scene.getMeshByName('finder-result-instances')).toBe(finderMesh);
    expect(scene.getMeshByName('selected-system-marker')).toBe(marker);
    expect(marker?.position.asArray()).toEqual([
      second.positionLy.x,
      second.positionLy.y,
      second.positionLy.z,
    ]);
    expectCameraPose(camera, initialCamera);
    expect(disposeScene).not.toHaveBeenCalled();

    tick(0);
    tick(1);
    tick(2);
    expect(events.at(-1)).toMatchObject({
      type: 'SCENE_APPLIED',
      sceneRevision: 2,
    });
  });

  it('keeps the exact top-down position and bearing in Babylon actual view matrices', () => {
    const engine = createEngine();
    const product = createBabylonGalaxyScene(engine, fixture);
    cleanup.push(() => engine.dispose());
    for (const bearingRad of [0, Math.PI / 2, Math.PI, -Math.PI / 2]) {
      const state = { ...initialCamera, pitchRad: Math.PI / 2, bearingRad };
      applyGalaxyCamera(product.camera, state);
      expect(product.camera.position.x).toBe(initialCamera.focusLy.x);
      expect(product.camera.position.z).toBe(initialCamera.focusLy.z);
      expectCameraPose(product.camera, state);
    }
  });

  it('retains state and mesh identities across resize while recomputing orthographic aspect', () => {
    const { engine, session, scene, camera, emit, events } = loadSession();
    const desired: CameraState = {
      ...initialCamera,
      projection: 'orthographic',
      bearingRad: 0.7,
      pitchRad: 1.1,
      revision: 2,
    };
    session.execute?.({ type: 'SET_CAMERA', camera: desired }, emit);
    const meshes = stableSceneMeshes(scene);
    const eventCount = events.length;
    let width = 640;
    let height = 360;
    vi.spyOn(engine, 'getRenderWidth').mockImplementation(() => width);
    vi.spyOn(engine, 'getRenderHeight').mockImplementation(() => height);
    const setSize = vi
      .spyOn(engine, 'setSize')
      .mockImplementation((nextWidth, nextHeight) => {
        width = nextWidth;
        height = nextHeight;
        return true;
      });

    session.resize({ width: 300, height: 900, dpr: 2 });

    expect(setSize).toHaveBeenCalledWith(600, 1_800, true);
    expectCameraPose(camera, desired);
    expect(camera.orthoRight! / camera.orthoTop!).toBeCloseTo(1 / 3);
    expect(stableSceneMeshes(scene)).toEqual(meshes);
    expect(scene.getMeshByName('galaxy-reference-grid-minor')).not.toBeNull();
    expect(events).toHaveLength(eventCount);
  });

  it('patches a newer catalogue-density generation without rebuilding the scene or moving the camera', () => {
    const { session, scene, camera, emit, events, tick } = loadSession();
    const finderMesh = scene.getMeshByName('finder-result-instances');
    const firstPayload = buildFixtureCatalogueDensity(
      [{ positionLy: { x: 20, y: -4, z: 30 } }],
      {
        generationId: 'fixture:live-density-v1',
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
    const firstContribution = createCatalogueDensityContribution(
      firstPayload,
      1,
    );

    expect(
      session.execute?.(
        { type: 'PATCH_CONTRIBUTION', contribution: firstContribution },
        emit,
      ),
    ).toEqual({ status: 'executed' });
    const firstDensityMesh = scene.getMeshByName(
      'catalogue-density-cells',
    ) as Mesh | null;
    expect(firstDensityMesh?.thinInstanceCount).toBe(1);
    expect(scene.activeCamera).toBe(camera);
    expect(scene.getMeshByName('finder-result-instances')).toBe(finderMesh);
    expectCameraPose(camera, initialCamera);

    const secondPayload = buildFixtureCatalogueDensity(
      [
        { positionLy: { x: 20, y: -4, z: 30 } },
        { positionLy: { x: 240, y: 12, z: -180 } },
      ],
      {
        generationId: 'fixture:live-density-v2',
        pyramidVersion: 'fixture-pyramid-v1',
        level: 2,
        cellSizeLy: 100,
        cellOriginLy: { x: -1_000, y: -1_000, z: -1_000 },
        boundsLy: {
          min: { x: -1_000, y: -1_000, z: -1_000 },
          max: { x: 1_000, y: 1_000, z: 1_000 },
        },
        coverageAsOf: '2026-09-13T01:00:00Z',
      },
    );
    const secondContribution = createCatalogueDensityContribution(
      secondPayload,
      2,
    );
    expect(
      session.execute?.(
        { type: 'PATCH_CONTRIBUTION', contribution: secondContribution },
        emit,
      ),
    ).toEqual({ status: 'executed' });

    const secondDensityMesh = scene.getMeshByName(
      'catalogue-density-cells',
    ) as Mesh | null;
    expect(firstDensityMesh?.isDisposed()).toBe(true);
    expect(secondDensityMesh).not.toBe(firstDensityMesh);
    expect(secondDensityMesh?.thinInstanceCount).toBe(2);
    expect(scene.activeCamera).toBe(camera);
    expect(scene.getMeshByName('finder-result-instances')).toBe(finderMesh);
    expectCameraPose(camera, initialCamera);

    expect(
      session.execute?.(
        { type: 'PATCH_CONTRIBUTION', contribution: firstContribution },
        emit,
      ),
    ).toEqual({ status: 'ignored', reason: 'stale' });
    expect(scene.getMeshByName('catalogue-density-cells')).toBe(
      secondDensityMesh,
    );

    tick(0);
    tick(1);
    tick(2);
    expect(
      events.filter((event) => event.type === 'CONTRIBUTION_APPLIED').at(-1),
    ).toMatchObject({
      contributionId: 'catalogue-density-base',
      contributionRevision: 2,
      renderedLayers: [
        {
          layerId: 'finder-systems',
          contributionRevision: 1,
        },
        {
          layerId: 'catalogue-density',
          contributionRevision: 2,
          sourceGeneration: 'fixture:live-density-v2',
          renderedTargetCount: 2,
        },
      ],
    });
  });

  it('animates flight on demand and publishes completion only when the destination is reached', () => {
    const { session, camera, emit, events, tick, scene } = loadSession();
    const meshes = stableSceneMeshes(scene);
    expect(
      session.execute?.(
        { type: 'FLY_TO', target: flightTarget, reducedMotion: false },
        emit,
      ),
    ).toEqual({ status: 'executed' });
    expect(changedCameras(events)).toHaveLength(1);
    expect(events.some((event) => event.type === 'TRANSITION_FINISHED')).toBe(
      false,
    );

    expect(tick(0)).toBe(true);
    expect(tick(350)).toBe(true);
    const middle = changedCameras(events).at(-1)!;
    expect(middle.focusLy.x).toBeCloseTo(
      (initialCamera.focusLy.x + destination.positionLy.x) / 2,
    );
    expect(middle.focusLy.y).toBeCloseTo(
      (initialCamera.focusLy.y + destination.positionLy.y) / 2,
    );
    expect(middle.distanceLy).toBeLessThan(initialCamera.distanceLy);
    expectCameraPose(camera, middle);
    expect(tick(699)).toBe(true);
    expect(events.some((event) => event.type === 'TRANSITION_FINISHED')).toBe(
      false,
    );
    expect(tick(700)).toBe(false);

    const final = changedCameras(events).at(-1)!;
    expect(final.focusLy).toEqual(destination.positionLy);
    expectCameraPose(camera, final);
    expect(
      events.filter((event) => event.type === 'TRANSITION_FINISHED'),
    ).toEqual([{ type: 'TRANSITION_FINISHED', target: flightTarget }]);
    tick(1_000);
    expect(
      events.filter((event) => event.type === 'TRANSITION_FINISHED'),
    ).toHaveLength(1);
    expect(stableSceneMeshes(scene)).toEqual(meshes);
    expect(scene.getMeshByName('galaxy-reference-grid-minor')).not.toBeNull();
  });

  describe('how close a flight to a selected system ends', () => {
    // A scene is built once, at the camera it was loaded with, and the commander
    // then zooms. The flight must not depend on how far out the scene was built.
    const builtAtOverview: GalaxySceneContract = {
      ...fixture,
      camera: { ...initialCamera, distanceLy: 250_000 },
    };
    const flyFrom = (builtAt: GalaxySceneContract, distanceLy: number) => {
      const loaded = loadSession(builtAt);
      loaded.session.execute?.(
        {
          type: 'SET_CAMERA',
          camera: { ...initialCamera, distanceLy, revision: 5 },
        },
        loaded.emit,
      );
      loaded.session.execute?.(
        { type: 'FLY_TO', target: flightTarget, reducedMotion: true },
        loaded.emit,
      );
      const final = changedCameras(loaded.events).at(-1)!;
      expect(final.focusLy).toEqual(destination.positionLy);
      return final;
    };

    it('zooms in to the local view from a scene built at the galaxy overview', () => {
      expect(flyFrom(builtAtOverview, 21_266).distanceLy).toBe(
        GALAXY_LOCAL_VIEW_DISTANCE_LY,
      );
      expect(flyFrom(builtAtOverview, 120_000).distanceLy).toBe(
        GALAXY_LOCAL_VIEW_DISTANCE_LY,
      );
    });

    it('ends at the same local view whatever distance the scene was built at', () => {
      expect(flyFrom(fixture, 5_000).distanceLy).toBe(
        GALAXY_LOCAL_VIEW_DISTANCE_LY,
      );
    });

    it('never zooms out, and never goes below the nearest useful distance', () => {
      expect(flyFrom(builtAtOverview, 150).distanceLy).toBeCloseTo(150, 6);
      expect(flyFrom(builtAtOverview, 3).distanceLy).toBeCloseTo(12, 6);
    });
  });

  it('applies reduced-motion flight immediately and completes exactly once', () => {
    const { session, camera, events, emit, tick } = loadSession();
    session.execute?.(
      { type: 'FLY_TO', target: flightTarget, reducedMotion: true },
      emit,
    );
    const final = changedCameras(events).at(-1)!;
    expect(final.focusLy).toEqual(destination.positionLy);
    expectCameraPose(camera, final);
    expect(events.at(-1)).toEqual({
      type: 'TRANSITION_FINISHED',
      target: flightTarget,
    });
    tick(5_000);
    expect(
      events.filter((event) => event.type === 'TRANSITION_FINISHED'),
    ).toHaveLength(1);
    expect(changedCameras(events)).toHaveLength(2);
  });

  it('cancels a flight on direct camera input without emitting stale completion', () => {
    const { session, camera, emit, events, tick } = loadSession();
    session.execute?.(
      { type: 'FLY_TO', target: flightTarget, reducedMotion: false },
      emit,
    );
    tick(250);
    const interrupted: CameraState = {
      ...changedCameras(events).at(-1)!,
      focusLy: { x: -230, y: 90, z: -670 },
      bearingRad: -0.5,
      revision: 10,
    };
    session.execute?.({ type: 'SET_CAMERA', camera: interrupted }, emit);
    tick(2_000);

    expect(changedCameras(events).at(-1)).toEqual(
      normalizeGalaxyCamera(interrupted),
    );
    expectCameraPose(camera, interrupted);
    expect(events.some((event) => event.type === 'TRANSITION_FINISHED')).toBe(
      false,
    );
  });

  it('rejects an unknown flight target without moving or producing completion', () => {
    const { session, camera, events, emit } = loadSession();
    expect(
      session.execute?.(
        {
          type: 'FLY_TO',
          target: { kind: 'system', systemId64: 'missing' },
          reducedMotion: false,
        },
        emit,
      ),
    ).toEqual({ status: 'unsupported', command: 'FLY_TO' });
    expectCameraPose(camera, initialCamera);
    expect(changedCameras(events)).toHaveLength(1);
    expect(events.some((event) => event.type === 'TRANSITION_FINISHED')).toBe(
      false,
    );
  });

  it('keeps an empty Finder source mesh disabled so it cannot appear as a false system', () => {
    const engine = createEngine();
    cleanup.push(() => engine.dispose());
    const product = createBabylonGalaxyScene(engine, {
      ...fixture,
      selection: [],
      contributions: [
        {
          ...fixture.contributions[0]!,
          layers: [
            {
              ...fixture.contributions[0]!.layers[0]!,
              payload: { systems: [] },
              targetCount: 0,
            },
          ],
        },
      ],
    });
    expect(product.finderStars.mesh.thinInstanceCount).toBe(0);
    expect(product.finderStars.mesh.isEnabled()).toBe(false);
    expect(product.points).toEqual([]);
    expect(product.selectedMarker).toBeNull();
  });

  it('picks a real system behind a translucent region fill before picking the region', () => {
    const regions = validateGalaxyRegionsPayload(
      JSON.parse(buildAuthoritativeGalaxyRegionsAsset()) as unknown,
    );
    const system = { ...destination, positionLy: { x: 0, y: -50, z: 0 } };
    const { session, scene, emit, events } = loadSession({
      ...fixture,
      camera: {
        ...initialCamera,
        focusLy: system.positionLy,
        pitchRad: Math.PI / 2,
      },
      selection: [],
      contributions: [
        createGalaxyRegionsContribution(regions, 1),
        {
          ...fixture.contributions[0]!,
          layers: [
            {
              ...fixture.contributions[0]!.layers[0]!,
              payload: { systems: [system] },
            },
          ],
        },
      ],
    });
    scene.updateTransformMatrix(true);
    for (const mesh of scene.meshes) mesh.computeWorldMatrix(true);
    const nearest = scene.pick(320, 180);
    expect(nearest?.hit).toBe(true);
    expect(nearest?.pickedMesh?.name).toMatch(/galaxy-region/);

    session.execute?.({ type: 'PICK', screenX: 320, screenY: 180 }, emit);

    expect(events.at(-1)).toEqual({
      type: 'TARGET_PICKED',
      target: flightTarget,
    });
  });
});
