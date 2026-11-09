import type { Camera } from '@babylonjs/core/Cameras/camera.js';
import { VertexBuffer } from '@babylonjs/core/Buffers/buffer.js';
import { BoundingInfo } from '@babylonjs/core/Culling/boundingInfo.js';
import { Constants } from '@babylonjs/core/Engines/constants.js';
import { Material } from '@babylonjs/core/Materials/material.js';
import { StandardMaterial } from '@babylonjs/core/Materials/standardMaterial.js';
import { RawTexture } from '@babylonjs/core/Materials/Textures/rawTexture.js';
import { Texture } from '@babylonjs/core/Materials/Textures/texture.js';
import { Color3 } from '@babylonjs/core/Maths/math.color.js';
import {
  Matrix,
  Quaternion,
  Vector3,
} from '@babylonjs/core/Maths/math.vector.js';
import { Mesh } from '@babylonjs/core/Meshes/mesh.js';
import { VertexData } from '@babylonjs/core/Meshes/mesh.vertexData.js';
import '@babylonjs/core/Meshes/thinInstanceMesh.js';
import type { Scene } from '@babylonjs/core/scene.js';

import {
  CATALOGUE_DENSITY_MAXIMUM_SUPPORT_CELLS,
  catalogueDensityRadiusLy,
  type CatalogueDensitySceneLayer,
} from '../galaxy-density';

export const CATALOGUE_DENSITY_KERNEL_SIZE = 64;
export const CATALOGUE_DENSITY_PRESENTATION_VERSION = 'stellar-glow-v3';
/** Gaussian width as a fraction of the splat radius (about 0.7–0.9 cells). */
const KERNEL_SIGMA = 0.45;

type DensityHeatColour = readonly [
  red: number,
  green: number,
  blue: number,
  alpha: number,
];

function mix(left: number, right: number, amount: number): number {
  return left + (right - left) * amount;
}

/**
 * Count-only stellar palette: cool sparse cells, warm luminous concentrations.
 * The alpha is a per-cell light contribution: neighbouring splats overlap and
 * add up, so it is kept low and steep enough that only dense regions saturate.
 */
export function catalogueDensityHeatColour(
  systemCount: number,
  maximumSystemCount: number,
): DensityHeatColour {
  if (!Number.isSafeInteger(systemCount) || systemCount <= 0) {
    throw new Error('Density colour requires a positive system count');
  }
  if (!Number.isSafeInteger(maximumSystemCount) || maximumSystemCount <= 0) {
    throw new Error('Density colour requires a positive maximum system count');
  }
  const fraction = Math.min(
    1,
    Math.log1p(systemCount) / Math.log1p(maximumSystemCount),
  );
  const cool = [0.2, 0.34, 0.55] as const;
  const dust = [0.62, 0.5, 0.58] as const;
  const core = [1, 0.84, 0.64] as const;
  const local = fraction < 0.56 ? fraction / 0.56 : (fraction - 0.56) / 0.44;
  const from = fraction < 0.56 ? cool : dust;
  const to = fraction < 0.56 ? dust : core;
  return [
    mix(from[0], to[0], local),
    mix(from[1], to[1], local),
    mix(from[2], to[2], local),
    0.02 + 0.2 * Math.pow(fraction, 2),
  ];
}

/**
 * Kernel opacity at a squared distance from the splat centre, in units of the
 * splat radius: a Gaussian tapered to exactly zero at the edge, so support
 * stays compact.
 */
export function catalogueDensityKernelAlpha(squaredRadius: number): number {
  if (!(squaredRadius >= 0) || squaredRadius >= 1) return 0;
  return (
    Math.exp(-squaredRadius / (2 * KERNEL_SIGMA ** 2)) *
    (1 - squaredRadius) ** 2
  );
}

/**
 * A compact presentation glow, not an occupancy map. Alpha is exactly zero
 * outside the unit disc; each instance scales it to its declared source-cell
 * radius. The same kernel is used for every cell on both rendering backends.
 */
export function buildCatalogueDensityKernel(): Uint8Array {
  const size = CATALOGUE_DENSITY_KERNEL_SIZE;
  const pixels = new Uint8Array(size * size * 4);
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const u = (2 * x) / (size - 1) - 1;
      const v = (2 * y) / (size - 1) - 1;
      const alpha = catalogueDensityKernelAlpha(u * u + v * v);
      const offset = (y * size + x) * 4;
      pixels[offset] = 255;
      pixels[offset + 1] = 255;
      pixels[offset + 2] = 255;
      pixels[offset + 3] = Math.round(255 * alpha);
    }
  }
  return pixels;
}

/**
 * Share one camera-facing quad across all static instance matrices. Babylon's
 * mesh billboard mode would rotate thin-instance translations around the mesh
 * origin. Rotating these four local vertices instead preserves every centroid
 * and uploads a constant amount of geometry only when camera orientation moves.
 */
function attachDensityBillboard(scene: Scene, mesh: Mesh): void {
  const right = Vector3.Right();
  const up = Vector3.Up();
  const previousRight = new Vector3(Number.NaN, Number.NaN, Number.NaN);
  const previousUp = previousRight.clone();
  const positions = new Float32Array(12);
  const corners = [
    [-1, -1],
    [1, -1],
    [1, 1],
    [-1, 1],
  ] as const;

  const faceCamera = (camera: Camera): void => {
    camera.getViewMatrix();
    const world = camera.getWorldMatrix().m;
    right.set(world[0], world[1], world[2]).normalize();
    up.set(world[4], world[5], world[6]).normalize();
    if (right.equals(previousRight) && up.equals(previousUp)) return;
    previousRight.copyFrom(right);
    previousUp.copyFrom(up);
    for (let index = 0; index < corners.length; index += 1) {
      const [x, y] = corners[index];
      const offset = index * 3;
      positions[offset] = right.x * x + up.x * y;
      positions[offset + 1] = right.y * x + up.y * y;
      positions[offset + 2] = right.z * x + up.z * y;
    }
    // Bounds already enclose every possible quad orientation.
    mesh.updateVerticesData(VertexBuffer.PositionKind, positions, false);
  };

  if (scene.activeCamera) faceCamera(scene.activeCamera);
  const observer = scene.onBeforeCameraRenderObservable.add(faceCamera);
  mesh.onDisposeObservable.addOnce(() => {
    scene.onBeforeCameraRenderObservable.remove(observer);
  });
}

/** One factual occupied cell becomes one soft splat at its source centroid. */
export function createCatalogueDensityMesh(
  scene: Scene,
  density: CatalogueDensitySceneLayer | null,
): Mesh | null {
  if (!density || density.payload.cells.length === 0) return null;

  const mesh = new Mesh('catalogue-density-cells', scene);
  const geometry = new VertexData();
  geometry.positions = [-1, -1, 0, 1, -1, 0, 1, 1, 0, -1, 1, 0];
  geometry.normals = [0, 0, -1, 0, 0, -1, 0, 0, -1, 0, 0, -1];
  geometry.uvs = [0, 0, 1, 0, 1, 1, 0, 1];
  geometry.indices = [0, 2, 1, 0, 3, 2];
  geometry.applyToMesh(mesh, true);
  mesh.isPickable = false;
  mesh.alphaIndex = 1;
  mesh.thinInstanceEnablePicking = false;
  mesh.doNotSyncBoundingInfo = true;
  mesh.hasVertexAlpha = true;

  const kernel = RawTexture.CreateRGBATexture(
    buildCatalogueDensityKernel(),
    CATALOGUE_DENSITY_KERNEL_SIZE,
    CATALOGUE_DENSITY_KERNEL_SIZE,
    scene,
    false,
    false,
    Texture.BILINEAR_SAMPLINGMODE,
  );
  kernel.name = 'catalogue-density-presentation-kernel';
  kernel.hasAlpha = true;
  kernel.gammaSpace = false;
  kernel.wrapU = Texture.CLAMP_ADDRESSMODE;
  kernel.wrapV = Texture.CLAMP_ADDRESSMODE;

  const material = new StandardMaterial('catalogue-density-material', scene);
  material.disableLighting = true;
  material.diffuseColor = Color3.White();
  material.emissiveColor = new Color3(0.72, 0.72, 0.72);
  material.specularColor = Color3.Black();
  Object.assign(material, { useVertexColors: true });
  material.opacityTexture = kernel;
  material.alpha = 0.9;
  // Overlapping splats add up like light, so the result does not depend on
  // the (unsorted) instance order and equal neighbours form an even glow.
  material.alphaMode = Constants.ALPHA_ADD;
  material.transparencyMode = Material.MATERIAL_ALPHABLEND;
  material.disableDepthWrite = true;
  material.backFaceCulling = false;
  mesh.material = material;
  mesh.onDisposeObservable.addOnce(() => material.dispose(false, true));

  const maximumSystemCount = density.payload.cells.reduce(
    (maximum, cell) => Math.max(maximum, cell.systemCount),
    1,
  );
  const matrices = new Float32Array(density.payload.cells.length * 16);
  const colours = new Float32Array(density.payload.cells.length * 4);
  const minimum = new Vector3(Infinity, Infinity, Infinity);
  const maximum = new Vector3(-Infinity, -Infinity, -Infinity);
  density.payload.cells.forEach((cell, index) => {
    const radius = catalogueDensityRadiusLy(
      cell.systemCount,
      maximumSystemCount,
      density.payload.cellSizeLy,
    );
    const centre = new Vector3(
      cell.centroidLy.x,
      cell.centroidLy.y,
      cell.centroidLy.z,
    );
    Matrix.Compose(
      new Vector3(radius, radius, radius),
      Quaternion.Identity(),
      centre,
    ).copyToArray(matrices, index * 16);
    colours.set(
      catalogueDensityHeatColour(cell.systemCount, maximumSystemCount),
      index * 4,
    );

    // Transparent corners still participate in geometry culling. This fixed
    // bound encloses them under every camera rotation without an O(n) update.
    const extent = new Vector3(1, 1, 1).scaleInPlace(radius * Math.SQRT2);
    minimum.minimizeInPlace(centre.subtract(extent));
    maximum.maximizeInPlace(centre.add(extent));
  });
  mesh.thinInstanceSetBuffer('matrix', matrices, 16, true);
  mesh.thinInstanceSetBuffer('color', colours, 4, true);
  mesh.setBoundingInfo(new BoundingInfo(minimum, maximum));
  attachDensityBillboard(scene, mesh);
  mesh.metadata = {
    spatialLayer: {
      id: 'catalogue-density',
      representation: 'DERIVED',
      generationId: density.payload.generationId,
      pyramidVersion: density.payload.pyramidVersion,
      level: density.payload.level,
      sourceSystemCount: density.payload.sourceSystemCount,
      coveredSystemCount: density.payload.coveredSystemCount,
      complete: density.payload.complete,
      renderedCellCount: density.payload.cells.length,
      generatedOccupancy: false,
      presentation: {
        version: CATALOGUE_DENSITY_PRESENTATION_VERSION,
        primitive: 'camera-facing-thin-instance-quad',
        kernel: 'tapered-gaussian-alpha',
        position: 'source-cell-centroid',
        countTransfer: 'log1p-radius-opacity-and-stellar-palette',
        palette: 'cool-sparse-to-warm-dense',
        blend: 'additive',
        maximumSupportRadiusLy:
          density.payload.cellSizeLy * CATALOGUE_DENSITY_MAXIMUM_SUPPORT_CELLS,
        supportMeaning: 'presentation-glow-not-cell-occupancy',
      },
    },
  };
  return mesh;
}
