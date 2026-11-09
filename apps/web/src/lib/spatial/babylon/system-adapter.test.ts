import { describe, expect, it, vi } from 'vitest';
import { NullEngine } from '@babylonjs/core/Engines/nullEngine.js';
import { PickingInfo } from '@babylonjs/core/Collisions/pickingInfo.js';
import { Ray } from '@babylonjs/core/Culling/ray.js';
import { Matrix, Vector3 } from '@babylonjs/core/Maths/math.vector.js';
import { StandardMaterial } from '@babylonjs/core/Materials/standardMaterial.js';

import type { BodyVisualDescriptor, SystemSceneContract } from '../contracts';
import { buildSystemScene, type SystemOrbitGuide } from '../system-scene';
import { solLikeSystemDetail } from '../system-scene-fixtures';
import {
  applySystemCamera,
  createBabylonSession,
  createBabylonSystemScene,
} from './adapter';

function body(
  bodyId: number,
  name: string,
  parentId: number | null,
  distance?: number,
  bodyClass = 'Planet',
): BodyVisualDescriptor {
  return {
    ref: { systemId64: '42', bodyId },
    name,
    ...(parentId === null
      ? {}
      : { parent: { systemId64: '42', bodyId: parentId } }),
    class: { value: bodyClass, representation: 'AUTHORITATIVE' },
    ...(distance === undefined
      ? {}
      : {
          distanceFromArrivalLs: {
            value: distance,
            representation: 'AUTHORITATIVE',
          },
        }),
    displayRadius: bodyClass === 'Star' ? 2.2 : 0.68,
    orbital: { placement: 'DETERMINISTIC_SCHEMATIC' },
    rings: { state: 'UNKNOWN', bands: [] },
  };
}

function fixture(): SystemSceneContract {
  return {
    kind: 'system',
    revision: 1,
    systemId64: '42',
    fidelity: 'S1',
    camera: {
      systemId64: '42',
      focus: { kind: 'system', systemId64: '42' },
      semanticDistance: 80,
      bearingRad: -0.8,
      pitchRad: 0.6,
      revision: 1,
    },
    bodies: [
      body(0, 'Sol', null, 0, 'Star'),
      body(2, 'Earth', 0, 500),
      body(3, 'Luna', 2, 501),
      body(4, 'Second moon', 2, 502),
      body(5, 'Mars', 0, 1_000),
      body(8, 'Binary barycentre', null, 0, 'Barycentre'),
      body(9, 'Binary A', 8, 5, 'Star'),
      body(10, 'Binary B', 8, 10, 'Star'),
      body(11, 'Unattached', 999, 20),
      body(12, 'Moon with unknown distance', 2),
    ],
    infrastructure: [],
    contributions: [],
  };
}

describe('hierarchical System Map Babylon presentation', () => {
  it.each([1200, 1280])(
    'frames every Sol-like glyph and ring in a %s by 684 desktop map',
    (width) => {
      const engine = new NullEngine({
        renderWidth: width,
        renderHeight: 684,
        textureSize: 512,
        deterministicLockstep: true,
        lockstepMaxSteps: 1,
      });
      const contract = buildSystemScene(solLikeSystemDetail(), 1);
      const product = createBabylonSystemScene(engine, contract);
      product.scene.updateTransformMatrix(true);
      const viewport = product.camera.viewport.toGlobal(width, 684);
      const project = (position: Vector3) =>
        Vector3.Project(
          position,
          Matrix.Identity(),
          product.scene.getTransformMatrix(),
          viewport,
        );
      expect(contract.camera.semanticDistance).toBeLessThanOrEqual(60);
      expect(product.bodies).toHaveLength(41);
      for (const entry of product.bodies) {
        for (const mesh of [entry.mesh, ...entry.ringMeshes]) {
          mesh.computeWorldMatrix(true);
          for (const corner of mesh.getBoundingInfo().boundingBox
            .vectorsWorld) {
            const point = project(corner);
            expect(point.x, `${mesh.name} left edge`).toBeGreaterThanOrEqual(0);
            expect(point.x, `${mesh.name} right edge`).toBeLessThanOrEqual(
              width,
            );
            expect(point.y, `${mesh.name} top edge`).toBeGreaterThanOrEqual(0);
            expect(point.y, `${mesh.name} bottom edge`).toBeLessThanOrEqual(
              684,
            );
            expect(point.z).toBeGreaterThan(0);
            expect(point.z).toBeLessThan(1);
          }
        }
        if (entry.layout.body.ref.bodyId >= 100) {
          const hit = product.scene.pickWithRay(
            new Ray(
              product.camera.position,
              entry.mesh.position.subtract(product.camera.position).normalize(),
            ),
            (mesh) => mesh === entry.mesh,
          );
          expect(hit?.pickedMesh?.metadata.spatialTarget).toEqual({
            kind: 'body',
            ref: entry.layout.body.ref,
          });
        }
      }
      const byId = new Map(
        product.bodies.map((entry) => [entry.layout.body.ref.bodyId, entry]),
      );
      const earth = byId.get(3)!;
      const jupiter = byId.get(5)!;
      expect(earth.mesh.metadata.bodyFamily).toEqual({ family: 'earth-like' });
      expect(jupiter.mesh.metadata.bodyFamily).toEqual({
        family: 'gas-giant',
        variant: 'I',
      });
      expect(
        (earth.mesh.material as StandardMaterial).diffuseColor.asArray(),
      ).not.toEqual(
        (jupiter.mesh.material as StandardMaterial).diffuseColor.asArray(),
      );
      expect(jupiter.mesh.getVerticesData('color')).not.toBeNull();
      expect(byId.get(6)!.ringMeshes).toHaveLength(1);
      expect(byId.get(7)!.ringMeshes).toHaveLength(1);
      product.scene.dispose();
      engine.dispose();
    },
  );

  it('keeps large hierarchy fits inside the camera far plane and guards non-finite distances', () => {
    const engine = new NullEngine();
    const contract = fixture();
    const product = createBabylonSystemScene(engine, {
      ...contract,
      camera: { ...contract.camera, semanticDistance: 1_000_000 },
    });
    expect(product.camera.position.length()).toBeCloseTo(1_000_000);
    for (const entry of product.bodies) {
      expect(
        Vector3.Distance(product.camera.position, entry.mesh.position) +
          entry.layout.body.displayRadius,
      ).toBeLessThan(product.camera.maxZ);
    }
    for (const semanticDistance of [Number.NaN, Infinity, Number.MAX_VALUE]) {
      applySystemCamera(
        product.camera,
        { ...contract.camera, semanticDistance },
        product.bodies,
      );
      expect(product.camera.position.asArray().every(Number.isFinite)).toBe(
        true,
      );
      expect(Number.isFinite(product.camera.maxZ)).toBe(true);
      expect(product.camera.maxZ).toBeGreaterThan(
        product.camera.position.length(),
      );
    }
    product.scene.dispose();
    engine.dispose();
  });

  it('draws a faint guide at each placed child orbit, with distinct pickable unknown and barycentre markers', () => {
    const engine = new NullEngine();
    const product = createBabylonSystemScene(engine, fixture());
    const guides = product.orbitMesh?.metadata.guides as SystemOrbitGuide[];
    expect(
      guides.map(({ parentBodyId, bodyId }) => ({ parentBodyId, bodyId })),
    ).toEqual([
      { parentBodyId: 0, bodyId: 2 },
      { parentBodyId: 0, bodyId: 5 },
      { parentBodyId: 2, bodyId: 3 },
      { parentBodyId: 2, bodyId: 4 },
      { parentBodyId: 8, bodyId: 9 },
      { parentBodyId: 8, bodyId: 10 },
    ]);
    expect(product.orbitMesh?.isPickable).toBe(false);
    expect(product.orbitMesh?.alpha).toBeLessThan(0.5);
    expect(product.orbitMesh?.getTotalVertices()).toBe(guides.length * 97);
    const vertices = product.orbitMesh!.getVerticesData('position')!;
    guides.forEach((guide, index) => {
      const parent = product.bodies.find(
        (entry) => entry.layout.body.ref.bodyId === guide.parentBodyId,
      )!;
      const child = product.bodies.find(
        (entry) => entry.layout.body.ref.bodyId === guide.bodyId,
      )!;
      expect(guide.position).toEqual(parent.layout.position);
      expect(guide.radius).toBe(child.layout.orbitRadius);
      expect(guide.radius).toBeCloseTo(
        Vector3.Distance(parent.mesh.position, child.mesh.position),
      );
      expect(child.mesh.metadata.spatialTarget).toEqual({
        kind: 'body',
        ref: { systemId64: '42', bodyId: guide.bodyId },
      });
      child.mesh.computeWorldMatrix(true);
      expect(
        product.scene.pickWithRay(
          new Ray(
            child.mesh.position.add(new Vector3(0, 10, 0)),
            new Vector3(0, -1, 0),
          ),
          (mesh) => mesh === child.mesh,
        )?.pickedMesh,
      ).toBe(child.mesh);
      const start = index * 97 * 3;
      for (let vertex = 0; vertex < 97; vertex++) {
        const offset = start + vertex * 3;
        expect(vertices[offset + 1]).toBeCloseTo(guide.position.y, 4);
        expect(
          Math.hypot(
            vertices[offset]! - guide.position.x,
            vertices[offset + 2]! - guide.position.z,
          ),
        ).toBeCloseTo(guide.radius, 4);
      }
    });
    expect(product.parentLinkMesh).toBeNull();
    for (const id of [11, 12]) {
      const unknown = product.bodies.find(
        (entry) => entry.layout.body.ref.bodyId === id,
      )!;
      expect(unknown.mesh.getClassName()).toBe('LinesMesh');
      expect(unknown.mesh.isPickable).toBe(true);
      expect(unknown.mesh.metadata).toMatchObject({
        markerKind: 'position-unknown',
        spatialTarget: { kind: 'body', ref: { systemId64: '42', bodyId: id } },
      });
      expect(unknown.mesh.position.asArray()).not.toEqual([0, 0, 0]);
      unknown.mesh.computeWorldMatrix(true);
      const hit = product.scene.pickWithRay(
        new Ray(
          unknown.mesh.position.add(
            new Vector3(unknown.layout.body.displayRadius, 10, 0),
          ),
          new Vector3(0, -1, 0),
        ),
        (mesh) => mesh === unknown.mesh,
      );
      expect(hit?.pickedMesh).toBe(unknown.mesh);
    }
    const barycentre = product.bodies.find(
      (entry) => entry.layout.body.ref.bodyId === 8,
    )!;
    expect(barycentre.mesh.getClassName()).toBe('LinesMesh');
    expect(barycentre.mesh.getTotalVertices()).toBe(4);
    expect(barycentre.mesh.metadata.markerKind).toBe('barycentre');
    barycentre.mesh.computeWorldMatrix(true);
    expect(
      product.scene.pickWithRay(
        new Ray(
          barycentre.mesh.position.add(new Vector3(0, 10, 0)),
          new Vector3(0, -1, 0),
        ),
        (mesh) => mesh === barycentre.mesh,
      )?.pickedMesh,
    ).toBe(barycentre.mesh);
    const orbitMesh = product.orbitMesh!;
    product.scene.dispose();
    expect(orbitMesh.isDisposed()).toBe(true);
    expect(barycentre.mesh.isDisposed()).toBe(true);
    engine.dispose();
  });

  it('does not claim an orbit radius when every child has unknown orbital distance', () => {
    const engine = new NullEngine();
    const product = createBabylonSystemScene(engine, {
      ...fixture(),
      bodies: [body(0, 'Sol', null, 0, 'Star'), body(2, 'Unplaced planet', 0)],
    });
    const [, child] = product.bodies;
    expect(child!.layout.placement).toBe('unplaced');
    expect(child!.layout.orbitRadius).toBe(0);
    expect(child!.mesh.metadata.markerKind).toBe('position-unknown');
    expect(product.orbitMesh).toBeNull();
    expect(product.parentLinkMesh).toBeNull();
    product.scene.dispose();
    engine.dispose();
  });

  it.each([1, 2])(
    'gives compact moons a six CSS pixel pick and hover radius at DPR %s',
    (dpr) => {
      const engine = new NullEngine({
        renderWidth: 1280 * dpr,
        renderHeight: 900 * dpr,
        textureSize: 512,
        deterministicLockstep: true,
        lockstepMaxSteps: 1,
      });
      vi.spyOn(engine, 'getRenderingCanvas').mockReturnValue({
        clientWidth: 1280,
        clientHeight: 900,
      } as HTMLCanvasElement);
      const session = createBabylonSession(engine, 'WEBGL2');
      const emit = vi.fn();
      const contract = fixture();
      session.execute?.(
        {
          type: 'LOAD_SCENE',
          scene: {
            ...contract,
            camera: { ...contract.camera, semanticDistance: 60 },
            bodies: [
              body(0, 'Sol', null, 0, 'Star'),
              body(2, 'Earth', 0, 500),
              { ...body(3, 'Luna', 2, 501), displayRadius: 0.075 },
            ],
          },
        },
        emit,
      );
      const scene = engine.scenes.at(-1)!;
      const moon = scene.getMeshByName('system-body-3')!;
      // Isolate the tiny glyph from the parent's larger native hit area.
      moon.position.set(6, 0, 0);
      scene.getMeshByName('system-body-2')!.position.set(-6, 0, 0);
      scene.updateTransformMatrix(true);
      for (const mesh of scene.meshes) mesh.computeWorldMatrix(true);
      const centre = Vector3.Project(
        moon.getAbsolutePosition(),
        Matrix.Identity(),
        scene.getTransformMatrix(),
        scene.activeCamera!.viewport.toGlobal(
          engine.getRenderWidth(),
          engine.getRenderHeight(),
        ),
      );
      const screenX = centre.x / dpr + 5;
      const screenY = centre.y / dpr;
      expect(scene.pick(screenX * dpr, screenY * dpr)?.hit).toBe(false);
      for (const type of ['PICK', 'HOVER'] as const) {
        session.execute?.({ type, screenX, screenY }, emit);
        expect(emit).toHaveBeenLastCalledWith(
          type === 'PICK'
            ? {
                type: 'TARGET_PICKED',
                target: { kind: 'body', ref: { systemId64: '42', bodyId: 3 } },
              }
            : {
                type: 'TARGET_HOVERED',
                target: { kind: 'body', ref: { systemId64: '42', bodyId: 3 } },
                screenX,
                screenY,
              },
        );
      }
      session.execute?.(
        { type: 'PICK', screenX: centre.x / dpr + 7, screenY },
        emit,
      );
      expect(emit).toHaveBeenLastCalledWith({
        type: 'TARGET_PICKED',
        target: undefined,
      });
      session.dispose();
    },
  );

  it.each(['behind', 'offscreen', 'hidden', 'disabled', 'unpickable'])(
    'does not extend the pick area of a %s body',
    (state) => {
      const engine = new NullEngine();
      const session = createBabylonSession(engine, 'WEBGL2');
      const emit = vi.fn();
      session.execute?.(
        {
          type: 'LOAD_SCENE',
          scene: {
            ...fixture(),
            bodies: [
              { ...body(3, 'Small body', null, 0), displayRadius: 0.075 },
            ],
          },
        },
        emit,
      );
      const scene = engine.scenes.at(-1)!;
      const camera = scene.activeCamera!;
      const mesh = scene.getMeshByName('system-body-3')!;
      if (state === 'behind') {
        mesh.position.copyFrom(
          camera.position.subtract(camera.getForwardRay().direction.scale(10)),
        );
      } else if (state === 'offscreen') {
        mesh.position.set(10_000, 0, 0);
      } else if (state === 'hidden') {
        mesh.isVisible = false;
      } else if (state === 'disabled') {
        mesh.setEnabled(false);
      } else {
        mesh.isPickable = false;
      }
      scene.updateTransformMatrix(true);
      mesh.computeWorldMatrix(true);
      const projected = Vector3.Project(
        mesh.getAbsolutePosition(),
        Matrix.Identity(),
        scene.getTransformMatrix(),
        camera.viewport.toGlobal(
          engine.getRenderWidth(),
          engine.getRenderHeight(),
        ),
      );
      // Isolate the tolerance path even if Babylon changes native marker picking.
      vi.spyOn(scene, 'pick').mockReturnValue(new PickingInfo());
      session.execute?.(
        { type: 'PICK', screenX: projected.x, screenY: projected.y },
        emit,
      );
      expect(emit).toHaveBeenLastCalledWith({
        type: 'TARGET_PICKED',
        target: undefined,
      });
      session.dispose();
    },
  );

  it('renders only selected or hovered body links and disposes replaced interaction geometry', () => {
    const engine = new NullEngine();
    const session = createBabylonSession(engine, 'WEBGL2');
    const emit = vi.fn();
    const contract = fixture();
    session.execute?.({ type: 'LOAD_SCENE', scene: contract }, emit);
    const scene = engine.scenes.at(-1)!;
    const links = () => scene.getMeshByName('system-parent-links');
    expect(links()).toBeNull();

    const selectedCamera = {
      ...contract.camera,
      focus: { kind: 'body' as const, ref: { systemId64: '42', bodyId: 2 } },
      revision: 2,
    };
    session.execute?.({ type: 'SET_CAMERA', camera: selectedCamera }, emit);
    const selectedLinks = links()!;
    expect(selectedLinks.metadata.links).toEqual([
      { parentBodyId: 0, bodyId: 2 },
      { parentBodyId: 2, bodyId: 3 },
      { parentBodyId: 2, bodyId: 4 },
      { parentBodyId: 2, bodyId: 12 },
    ]);
    expect(selectedLinks.isPickable).toBe(false);
    session.execute?.(
      { type: 'SET_CAMERA', camera: { ...selectedCamera, bearingRad: 0.2 } },
      emit,
    );
    expect(links()).toBe(selectedLinks);

    const hit = new PickingInfo();
    hit.hit = true;
    hit.pickedMesh = scene.getMeshByName('system-body-9');
    vi.spyOn(scene, 'pick').mockReturnValue(hit);
    session.execute?.({ type: 'HOVER', screenX: 20, screenY: 30 }, emit);
    const hoverLinks = links()!;
    expect(selectedLinks.isDisposed()).toBe(true);
    expect(hoverLinks.metadata.links).toContainEqual({
      parentBodyId: 8,
      bodyId: 9,
    });
    expect(hoverLinks.metadata.links).not.toContainEqual({
      parentBodyId: 8,
      bodyId: 10,
    });
    expect(hoverLinks.metadata.links).toHaveLength(5);
    expect(emit).toHaveBeenCalledWith(
      expect.objectContaining({
        type: 'TARGET_HOVERED',
        target: { kind: 'body', ref: { systemId64: '42', bodyId: 9 } },
      }),
    );

    session.execute?.({ type: 'CLEAR_HOVER' }, emit);
    expect(hoverLinks.isDisposed()).toBe(true);
    expect(links()!.metadata.links).toHaveLength(4);
    const restoredLinks = links()!;
    session.execute?.({ type: 'SET_CAMERA', camera: contract.camera }, emit);
    expect(restoredLinks.isDisposed()).toBe(true);
    expect(links()).toBeNull();

    hit.pickedMesh = scene.getMeshByName('system-body-11');
    session.execute?.({ type: 'PICK', screenX: 20, screenY: 30 }, emit);
    expect(emit).toHaveBeenCalledWith({
      type: 'TARGET_PICKED',
      target: { kind: 'body', ref: { systemId64: '42', bodyId: 11 } },
    });
    session.dispose();
  });
});
