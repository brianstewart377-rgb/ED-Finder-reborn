import { describe, expect, it } from 'vitest';
import { NullEngine } from '@babylonjs/core/Engines/nullEngine.js';
import { FreeCamera } from '@babylonjs/core/Cameras/freeCamera.js';
import { Matrix, Vector3 } from '@babylonjs/core/Maths/math.vector.js';
import { Scene } from '@babylonjs/core/scene.js';

import {
  SYSTEM_CAMERA_FOV_RAD,
  systemCameraProjector,
  systemGlyphRadiusPx,
} from '../system-labels';
import { solLikeSystemDetail } from '../system-scene-fixtures';
import { buildSystemScene } from '../system-scene';
import { applySystemCamera, createBabylonSystemScene } from './adapter';

// The label overlay is plain geometry (`../system-labels.ts`). It lives outside
// the renderer adapter, so the one place that may import Babylon pins that
// geometry to what Babylon itself projects. A change to either side fails here.
describe('System Map label projection against Babylon', () => {
  it('uses the field of view Babylon gives the system camera', () => {
    const engine = new NullEngine();
    const probe = new FreeCamera('probe', Vector3.Zero(), new Scene(engine));
    expect(probe.fov).toBe(SYSTEM_CAMERA_FOV_RAD);
    expect(probe.fovMode).toBe(FreeCamera.FOVMODE_VERTICAL_FIXED);
    engine.dispose();
  });

  it.each([
    [1200, 684, {}],
    [640, 900, { bearingRad: 1.9, pitchRad: 1.1 }],
    [1707, 700, { bearingRad: -2.6, pitchRad: 0.2, semanticDistance: 24 }],
    [800, 800, { pitchRad: 9 }],
    [800, 800, { pitchRad: -1, semanticDistance: Number.NaN }],
  ])(
    'puts every body exactly where Babylon does in a %i by %i canvas %o',
    (width, height, patch) => {
      const engine = new NullEngine({
        renderWidth: width,
        renderHeight: height,
        textureSize: 512,
        deterministicLockstep: true,
        lockstepMaxSteps: 1,
      });
      const contract = buildSystemScene(solLikeSystemDetail(), 1);
      const product = createBabylonSystemScene(engine, contract);
      const earth = product.bodies.find(
        (entry) => entry.layout.body.name === 'Earth',
      )!;
      // Both an overview and a camera centred on a body, as the commander can do.
      for (const focus of [
        contract.camera.focus,
        {
          kind: 'body' as const,
          ref: earth.layout.body.ref,
        },
      ]) {
        const state = { ...contract.camera, ...patch, focus };
        applySystemCamera(product.camera, state, product.bodies);
        product.scene.updateTransformMatrix(true);
        const viewport = product.camera.viewport.toGlobal(width, height);
        const target =
          focus.kind === 'body' ? earth.layout.position : { x: 0, y: 0, z: 0 };
        const project = systemCameraProjector(state, target, {
          width,
          height,
        })!;
        expect(project).not.toBeNull();
        let compared = 0;
        for (const entry of product.bodies) {
          const babylon = Vector3.Project(
            entry.mesh.position,
            Matrix.Identity(),
            product.scene.getTransformMatrix(),
            viewport,
          );
          const mine = project(
            entry.layout.position.x,
            entry.layout.position.y,
            entry.layout.position.z,
          );
          const viewZ = Vector3.TransformCoordinates(
            entry.mesh.position,
            product.scene.getViewMatrix(),
          ).z;
          // Behind the camera is the one place Babylon has no useful answer.
          expect(mine === null, `${entry.layout.body.name} visibility`).toBe(
            !(viewZ > 0),
          );
          if (!mine) continue;
          compared += 1;
          expect(mine.x, `${entry.layout.body.name} x`).toBeCloseTo(
            babylon.x,
            2,
          );
          expect(mine.y, `${entry.layout.body.name} y`).toBeCloseTo(
            babylon.y,
            2,
          );
        }
        expect(compared).toBeGreaterThan(20);
      }
      product.scene.dispose();
      engine.dispose();
    },
  );

  it('sizes a glyph the way its projected silhouette is sized', () => {
    const width = 1000;
    const height = 600;
    const engine = new NullEngine({
      renderWidth: width,
      renderHeight: height,
      textureSize: 512,
      deterministicLockstep: true,
      lockstepMaxSteps: 1,
    });
    const contract = buildSystemScene(solLikeSystemDetail(), 1);
    const product = createBabylonSystemScene(engine, contract);
    const jupiter = product.bodies.find(
      (entry) => entry.layout.body.name === 'Jupiter',
    )!;
    applySystemCamera(product.camera, contract.camera, product.bodies);
    product.scene.updateTransformMatrix(true);
    const viewport = product.camera.viewport.toGlobal(width, height);
    const toScreen = (point: Vector3) =>
      Vector3.Project(
        point,
        Matrix.Identity(),
        product.scene.getTransformMatrix(),
        viewport,
      );
    const radius = jupiter.layout.body.displayRadius;
    // One glyph-radius from the centre, straight up the view plane.
    const viewUp = product.camera.getDirection(Vector3.Up());
    const centre = toScreen(jupiter.mesh.position);
    const rim = toScreen(jupiter.mesh.position.add(viewUp.scale(radius)));
    const project = systemCameraProjector(
      contract.camera,
      { x: 0, y: 0, z: 0 },
      { width, height },
    )!;
    const at = project(
      jupiter.layout.position.x,
      jupiter.layout.position.y,
      jupiter.layout.position.z,
    )!;
    expect(
      systemGlyphRadiusPx(radius, at.depth, { width, height }),
    ).toBeCloseTo(Math.abs(centre.y - rim.y), 2);
    product.scene.dispose();
    engine.dispose();
  });
});
