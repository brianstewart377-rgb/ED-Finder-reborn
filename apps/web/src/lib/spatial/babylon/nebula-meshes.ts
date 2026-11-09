import { BoundingInfo } from '@babylonjs/core/Culling/boundingInfo.js';
import { Constants } from '@babylonjs/core/Engines/constants.js';
import { Material } from '@babylonjs/core/Materials/material.js';
import { ShaderMaterial } from '@babylonjs/core/Materials/shaderMaterial.js';
import { RawTexture } from '@babylonjs/core/Materials/Textures/rawTexture.js';
import { Texture } from '@babylonjs/core/Materials/Textures/texture.js';
import {
  Matrix,
  Quaternion,
  Vector3,
} from '@babylonjs/core/Maths/math.vector.js';
import { Mesh } from '@babylonjs/core/Meshes/mesh.js';
import { VertexData } from '@babylonjs/core/Meshes/mesh.vertexData.js';
import '@babylonjs/core/Meshes/thinInstanceMesh.js';
import '@babylonjs/core/Shaders/ShadersInclude/instancesDeclaration.js';
import '@babylonjs/core/Shaders/ShadersInclude/instancesVertex.js';
import type { Scene } from '@babylonjs/core/scene.js';

import type { GalaxyNebula } from '../galaxy-nebulae';
import {
  NEBULA_LANDMARK_MARKER_RADIUS_LY,
  nebulaCloudRadiusLy,
} from '../galaxy-nebulae-policy';
import { createNebulaCloudAtlas } from './nebula-cloud-atlas';

export const NEBULA_CLOUD_QUADS_PER_LANDMARK = 3;

// Keep the established emission-nebula palette family: rose, teal, gold,
// violet, cyan and jade. These colours describe presentation, not observations.
const NEBULA_PALETTE = [
  [0.86, 0.34, 0.52],
  [0.3, 0.68, 0.74],
  [0.9, 0.6, 0.3],
  [0.58, 0.4, 0.82],
  [0.34, 0.6, 0.86],
  [0.4, 0.74, 0.54],
] as const;

/** The existing deterministic 0..1 hash of the catalogue identity. */
function nebulaIdentityHash(id: string): number {
  let hash = 2_166_136_261;
  for (let index = 0; index < id.length; index += 1) {
    hash ^= id.charCodeAt(index);
    hash = Math.imul(hash, 16_777_619);
  }
  return ((hash >>> 0) % 100_000) / 100_000;
}

function paletteColour(nebula: GalaxyNebula, hash: number) {
  const index =
    (nebula.regionId ?? Math.floor(hash * NEBULA_PALETTE.length)) %
    NEBULA_PALETTE.length;
  return NEBULA_PALETTE[index]!;
}

const cloudVertexSource = `
precision highp float;
attribute vec3 position;
attribute vec2 uv;
attribute vec4 cloudVariation;
uniform mat4 view;
uniform mat4 viewProjection;
uniform vec3 cameraPosition;
uniform float atlasTileScale;
varying vec2 vUv;
varying vec4 vColour;
varying float vApproachFade;
#include<instancesDeclaration>
void main(void) {
  #include<instancesVertex>
  vec3 centre = finalWorld[3].xyz;
  vec2 local = position.xy * vec2(length(finalWorld[0].xyz), length(finalWorld[1].xyz));
  vec2 rotated = vec2(
    local.x * cloudVariation.x - local.y * cloudVariation.y,
    local.x * cloudVariation.y + local.y * cloudVariation.x
  );
  vec3 right = vec3(view[0][0], view[1][0], view[2][0]);
  vec3 up = vec3(view[0][1], view[1][1], view[2][1]);
  gl_Position = viewProjection * vec4(centre + right * rotated.x + up * rotated.y, 1.0);
  vUv = (uv + cloudVariation.zw) * atlasTileScale;
  vColour = instanceColor;
  float radius = length(finalWorld[2].xyz);
  // A nearby cloud also fades if panning/orbiting brings the camera through it
  // while its focus distance remains large. No CPU per-instance frame work.
  vApproachFade = smoothstep(radius * 1.05, radius * 2.1, distance(centre, cameraPosition));
}`;

const cloudFragmentSource = `
precision highp float;
uniform sampler2D cloudAtlas;
uniform float cloudVisibility;
varying vec2 vUv;
varying vec4 vColour;
varying float vApproachFade;
void main(void) {
  float alpha = texture2D(cloudAtlas, vUv).a * vColour.a * cloudVisibility * vApproachFade;
  gl_FragColor = vec4(vColour.rgb, alpha);
}`;

const markerVertexSource = `
precision highp float;
attribute vec3 position;
attribute vec2 uv;
uniform mat4 view;
uniform mat4 projection;
uniform mat4 viewProjection;
uniform float viewportHeight;
uniform float markerRadiusLy;
varying vec2 vLocal;
varying vec4 vColour;
#include<instancesDeclaration>
void main(void) {
  #include<instancesVertex>
  vec3 centre = finalWorld[3].xyz;
  vec4 clipCentre = viewProjection * vec4(centre, 1.0);
  float unitsPerPixel = 2.0 * abs(clipCentre.w) / (projection[1][1] * viewportHeight);
  // A small 3-6 pixel landmark remains findable at every zoom. Display sizing
  // never changes its catalogue coordinate or suggests a measured extent.
  float radius = clamp(markerRadiusLy, unitsPerPixel * 1.5, unitsPerPixel * 3.0);
  vec3 right = vec3(view[0][0], view[1][0], view[2][0]);
  vec3 up = vec3(view[0][1], view[1][1], view[2][1]);
  gl_Position = viewProjection * vec4(centre + radius * (right * position.x + up * position.y), 1.0);
  vLocal = position.xy;
  vColour = instanceColor;
}`;

const markerFragmentSource = `
precision highp float;
varying vec2 vLocal;
varying vec4 vColour;
void main(void) {
  // The compact diamond distinguishes the reference position from its cloud.
  float diamond = abs(vLocal.x) + abs(vLocal.y);
  float alpha = 1.0 - smoothstep(0.52, 0.96, diamond);
  gl_FragColor = vec4(vColour.rgb, alpha * vColour.a);
}`;

function createNebulaPlane(name: string, scene: Scene): Mesh {
  const mesh = new Mesh(name, scene);
  const geometry = new VertexData();
  geometry.positions = [-1, -1, 0, 1, -1, 0, 1, 1, 0, -1, 1, 0];
  geometry.uvs = [0, 0, 1, 0, 1, 1, 0, 1];
  geometry.indices = [0, 2, 1, 0, 3, 2];
  geometry.applyToMesh(mesh);
  mesh.isPickable = false;
  mesh.thinInstanceEnablePicking = false;
  mesh.doNotSyncBoundingInfo = true;
  mesh.hasVertexAlpha = true;
  return mesh;
}

function configureNebulaMaterial(material: ShaderMaterial): void {
  material.alphaMode = Constants.ALPHA_ADD;
  material.transparencyMode = Material.MATERIAL_ALPHABLEND;
  material.disableDepthWrite = true;
  material.backFaceCulling = false;
}

/** One non-pickable marker per selected catalogue position; clouds cannot hide it. */
export function createGalaxyNebulaLandmarkMesh(
  scene: Scene,
  landmarks: readonly GalaxyNebula[],
): Mesh | null {
  if (!landmarks.length) return null;
  const mesh = createNebulaPlane('galaxy-nebula-landmarks', scene);
  // Shader screen sizing is camera dependent. Keep this single bounded batch
  // active so stale CPU bounds cannot cull a visible landmark on a viewport edge.
  mesh.alwaysSelectAsActiveMesh = true;
  mesh.alphaIndex = 2;
  const material = new ShaderMaterial(
    'galaxy-nebula-landmark-material',
    scene,
    { vertexSource: markerVertexSource, fragmentSource: markerFragmentSource },
    {
      attributes: ['position', 'uv'],
      uniforms: [
        'world',
        'view',
        'projection',
        'viewProjection',
        'viewportHeight',
        'markerRadiusLy',
      ],
      needAlphaBlending: true,
    },
  );
  configureNebulaMaterial(material);
  material.setFloat('markerRadiusLy', NEBULA_LANDMARK_MARKER_RADIUS_LY);
  material.onBindObservable.add(() => {
    material
      .getEffect()
      ?.setFloat(
        'viewportHeight',
        Math.max(1, scene.getEngine().getRenderHeight()),
      );
  });
  mesh.material = material;
  mesh.onDisposeObservable.addOnce(() => material.dispose());
  const matrices = new Float32Array(landmarks.length * 16);
  const colours = new Float32Array(landmarks.length * 4);
  landmarks.forEach((nebula, index) => {
    const { x, y, z } = nebula.positionLy.value;
    Matrix.Translation(x, y, z).copyToArray(matrices, index * 16);
    const colour = paletteColour(nebula, nebulaIdentityHash(nebula.id));
    colours.set([colour[0], colour[1], colour[2], 0.85], index * 4);
  });
  mesh.thinInstanceSetBuffer('matrix', matrices, 16, true);
  mesh.thinInstanceSetBuffer('color', colours, 4, true);
  return mesh;
}

/** Three soft, depth-tested quads per landmark, one atlas/material/draw call. */
export function createGalaxyNebulaCloudMesh(
  scene: Scene,
  landmarks: readonly GalaxyNebula[],
): Mesh | null {
  if (!landmarks.length) return null;
  const mesh = createNebulaPlane('galaxy-nebula-clouds', scene);
  // A fresh scene has not crossed the enter threshold yet. The adapter applies
  // the same hysteresis on initial load and subsequent camera changes.
  mesh.setEnabled(false);
  mesh.alphaIndex = 0;
  const atlas = createNebulaCloudAtlas();
  const texture = RawTexture.CreateRGBATexture(
    atlas.data,
    atlas.width,
    atlas.height,
    scene,
    false,
    false,
    Texture.BILINEAR_SAMPLINGMODE,
  );
  texture.name = 'galaxy-nebula-cloud-alpha-atlas';
  texture.hasAlpha = true;
  texture.gammaSpace = false;
  texture.wrapU = Texture.CLAMP_ADDRESSMODE;
  texture.wrapV = Texture.CLAMP_ADDRESSMODE;
  const material = new ShaderMaterial(
    'galaxy-nebula-cloud-material',
    scene,
    { vertexSource: cloudVertexSource, fragmentSource: cloudFragmentSource },
    {
      attributes: ['position', 'uv', 'cloudVariation'],
      uniforms: [
        'world',
        'view',
        'viewProjection',
        'cameraPosition',
        'atlasTileScale',
        'cloudVisibility',
      ],
      samplers: ['cloudAtlas'],
      needAlphaBlending: true,
    },
  );
  configureNebulaMaterial(material);
  material.setTexture('cloudAtlas', texture);
  material.setFloat('atlasTileScale', 1 / atlas.tilesPerAxis);
  material.onBindObservable.add(() => {
    material.getEffect()?.setFloat('cloudVisibility', mesh.visibility);
  });
  mesh.material = material;
  mesh.onDisposeObservable.addOnce(() => material.dispose(false, true));

  const instanceCount = landmarks.length * NEBULA_CLOUD_QUADS_PER_LANDMARK;
  const matrices = new Float32Array(instanceCount * 16);
  const colours = new Float32Array(instanceCount * 4);
  const variations = new Float32Array(instanceCount * 4);
  const minimum = new Vector3(Infinity, Infinity, Infinity);
  const maximum = new Vector3(-Infinity, -Infinity, -Infinity);
  landmarks.forEach((nebula, index) => {
    const hash = nebulaIdentityHash(nebula.id);
    const radius = nebulaCloudRadiusLy(hash);
    const colour = paletteColour(nebula, hash);
    const { x, y, z } = nebula.positionLy.value;
    for (let layer = 0; layer < NEBULA_CLOUD_QUADS_PER_LANDMARK; layer += 1) {
      const instance = index * NEBULA_CLOUD_QUADS_PER_LANDMARK + layer;
      const variation = (hash + layer * 0.381966) % 1;
      const angle = variation * Math.PI * 2;
      const tile = Math.floor(
        variation * atlas.tilesPerAxis * atlas.tilesPerAxis,
      );
      const centre = new Vector3(
        x + Math.cos(angle) * radius * 0.08,
        y + (variation - 0.5) * radius * 0.12,
        z + Math.sin(angle) * radius * 0.08,
      );
      // All visible support remains within the bounded schematic radius:
      // each lobe is <= 0.8r, its centroid offset <= 0.1r from the reference.
      Matrix.Compose(
        new Vector3(
          radius * (0.66 + variation * 0.14),
          radius * (0.48 + variation * 0.16),
          radius,
        ),
        Quaternion.Identity(),
        centre,
      ).copyToArray(matrices, instance * 16);
      variations.set(
        [
          Math.cos(angle),
          Math.sin(angle),
          tile % atlas.tilesPerAxis,
          Math.floor(tile / atlas.tilesPerAxis),
        ],
        instance * 4,
      );
      const shift = (variation - 0.5) * 0.08;
      colours.set(
        [
          colour[0] + shift,
          colour[1] - shift * 0.5,
          colour[2] + shift * 0.35,
          0.09 + variation * 0.035,
        ],
        instance * 4,
      );
      // Fixed bounds enclose every billboard orientation; camera movement never
      // rewrites instance buffers or allocates per-landmark frame objects.
      const extent = radius * Math.SQRT2;
      minimum.minimizeInPlaceFromFloats(
        centre.x - extent,
        centre.y - extent,
        centre.z - extent,
      );
      maximum.maximizeInPlaceFromFloats(
        centre.x + extent,
        centre.y + extent,
        centre.z + extent,
      );
    }
  });
  mesh.thinInstanceSetBuffer('matrix', matrices, 16, true);
  mesh.thinInstanceSetBuffer('color', colours, 4, true);
  mesh.thinInstanceSetBuffer('cloudVariation', variations, 4, true);
  mesh.setBoundingInfo(new BoundingInfo(minimum, maximum));
  return mesh;
}
