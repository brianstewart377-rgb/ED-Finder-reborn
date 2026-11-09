import { FreeCamera } from '@babylonjs/core/Cameras/freeCamera.js';
import { Camera } from '@babylonjs/core/Cameras/camera.js';
import '@babylonjs/core/Culling/ray.js';
import { Engine } from '@babylonjs/core/Engines/engine.js';
import type { AbstractEngine } from '@babylonjs/core/Engines/abstractEngine.js';
import { HemisphericLight } from '@babylonjs/core/Lights/hemisphericLight.js';
import { Color3, Color4 } from '@babylonjs/core/Maths/math.color.js';
import {
  Matrix,
  Quaternion,
  Vector3,
} from '@babylonjs/core/Maths/math.vector.js';
import { Viewport } from '@babylonjs/core/Maths/math.viewport.js';
import { Material } from '@babylonjs/core/Materials/material.js';
import { StandardMaterial } from '@babylonjs/core/Materials/standardMaterial.js';
import { ImageProcessingConfiguration } from '@babylonjs/core/Materials/imageProcessingConfiguration.js';
import { GlowLayer } from '@babylonjs/core/Layers/glowLayer.js';
import { CreateBox } from '@babylonjs/core/Meshes/Builders/boxBuilder.js';
import { CreateLineSystem } from '@babylonjs/core/Meshes/Builders/linesBuilder.js';
import { CreateSphere } from '@babylonjs/core/Meshes/Builders/sphereBuilder.js';
import { CreateTorus } from '@babylonjs/core/Meshes/Builders/torusBuilder.js';
import { Mesh } from '@babylonjs/core/Meshes/mesh.js';
import '@babylonjs/core/Meshes/thinInstanceMesh.js';
import { VertexData } from '@babylonjs/core/Meshes/mesh.vertexData.js';
import { Scene } from '@babylonjs/core/scene.js';

import type {
  CameraState,
  BodyVisualDescriptor,
  GalaxySceneContract,
  GalaxySystemPoint,
  GalaxySystemsPayload,
  RenderedLayerReceipt,
  RuntimeCommand,
  RuntimeCommandDispatchResult,
  RuntimeEvent,
  SpatialContribution,
  SpatialRendererBackend,
  SpatialRuntime,
  SpatialRuntimeStatusListener,
  SpatialTarget,
  SpatialViewport,
  SystemCameraState,
  SystemSceneContract,
} from '../contracts';
import {
  GALAXY_CAMERA_FOV_RAD,
  GALAXY_LOCAL_VIEW_DISTANCE_LY,
  galaxyCameraPose,
  normalizeGalaxyCamera,
  interpolateGalaxyCamera,
  focusGalaxyCamera,
} from '../galaxy-camera';
import {
  galaxyReferenceGrid,
  type GalaxyGridLine,
  type GalaxyReferenceGridSpec,
} from '../galaxy-grid';
import {
  catalogueDensitySceneLayer,
  type CatalogueDensitySceneLayer,
} from '../galaxy-density';
import {
  COMMANDER_HISTORY_LAYER_ID,
  type CommanderHistoryPayload,
  type CommanderVisitPoint,
} from '../commander-history';
import {
  GALAXY_NEBULAE_LAYER_ID,
  type GalaxyNebula,
  type GalaxyNebulaePayload,
} from '../galaxy-nebulae';
import {
  galaxyNebulaVisibility,
  nebulaCloudEnabled,
  selectGalaxyNebulaLandmarks,
  NEBULA_LANDMARK_CELL_LY,
  NEBULA_LANDMARK_LIMIT,
  NEBULA_LANDMARK_FADE_START_LY,
  NEBULA_LANDMARK_FADE_END_LY,
  NEBULA_CLOUD_REENTER_LY,
  NEBULA_LANDMARK_MARKER_RADIUS_LY,
  NEBULA_SCHEMATIC_RADIUS_MIN_LY,
  NEBULA_SCHEMATIC_RADIUS_MAX_LY,
  PLANETARY_NEBULA_FADE_START_LY,
  PLANETARY_NEBULA_FADE_END_LY,
  PLANETARY_NEBULA_MARKER_RADIUS_LY,
  type GalaxyNebulaLandmarks,
  type NebulaKind,
} from '../galaxy-nebulae-policy';
import { createCatalogueDensityMesh } from './catalogue-density';
import {
  createGalaxyNebulaCloudMesh,
  createGalaxyNebulaLandmarkMesh,
  NEBULA_CLOUD_QUADS_PER_LANDMARK,
} from './nebula-meshes';

import { densityCrossfadeT } from '../galaxy-density-crossfade';
import {
  DENSITY_CROSSFADE_NEAR_LY,
  DENSITY_CROSSFADE_FAR_LY,
} from '../galaxy-detail-policy';
import {
  buildGalaxyRegionFillGeometry,
  findGalaxyRegionAt,
  galaxyRegionsSceneLayer,
  type GalaxyRegionMapSource,
  type GalaxyRegionsSceneLayer,
} from '../galaxy-regions';
import {
  createManagedSpatialRuntime,
  type SpatialBackendSession,
  type SpatialBackendResourceListener,
} from '../lifecycle';
import {
  stellarPresentation,
  type StellarPresentation,
} from '../stellar-presentation';
import {
  bodyFamilyPresentation,
  systemBodyLayout,
  systemOrbitGuides,
  type SystemBodyLayout,
} from '../system-scene';

export { nebulaCloudRadiusLy } from '../galaxy-nebulae-policy';

const disposeEngine = (engine: AbstractEngine): void => {
  try {
    engine.dispose();
  } catch {
    // Partially initialized engines still need a bounded fallback path.
  }
};

const BABYLON_SCENE_WARMUP_FRAMES = 3;
// Region fills stay subordinate to factual stellar density, but interaction
// must remain unmistakable at whole-Galaxy scale. The previous 0.14 hover
// alpha was technically different yet visually disappeared behind the atlas
// rails on real displays.
const REGION_BASE_ALPHA = 0.065;
const REGION_SELECTED_ALPHA = 0.34;
const REGION_HOVERED_ALPHA = 0.28;

export function galaxyStarMarkerSizeLy(distanceLy: number): number {
  if (!Number.isFinite(distanceLy) || distanceLy <= 0) {
    throw new RangeError('Galaxy star marker distance must be positive');
  }
  // Roughly four screen pixels at ordinary desktop heights. The position stays
  // in exact light years; only the non-factual presentation radius is scaled.
  return Math.max(0.008, distanceLy * 0.006);
}

const createDiagnosticScene = (engine: AbstractEngine): Scene => {
  const scene = new Scene(engine);
  scene.clearColor = new Color4(0.015, 0.02, 0.03, 1);

  const camera = new FreeCamera(
    'spatial-foundation-camera',
    new Vector3(0, 0, -5),
    scene,
  );
  camera.setTarget(Vector3.Zero());
  scene.activeCamera = camera;

  const light = new HemisphericLight(
    'spatial-foundation-light',
    new Vector3(0.25, 1, -0.5),
    scene,
  );
  light.intensity = 0.9;

  const marker = CreateBox(
    'spatial-foundation-diagnostic-marker',
    { size: 1.5 },
    scene,
  );
  marker.rotation.set(0.35, 0.65, 0.1);
  const material = new StandardMaterial(
    'spatial-foundation-diagnostic-material',
    scene,
  );
  material.diffuseColor = new Color3(0.28, 0.68, 0.78);
  material.specularColor = Color3.Black();
  marker.material = material;

  return scene;
};

export type BabylonGalaxyStarBatch = Readonly<{
  kind: 'catalogue' | 'finder';
  mesh: Mesh;
  points: readonly GalaxySystemPoint[];
  instanceMatrices: Float32Array;
  instanceColours: Float32Array;
  stellarPresentations: readonly StellarPresentation[];
  accentMeshes: readonly Mesh[];
}>;

export type BabylonGalaxyProduct = Readonly<{
  scene: Scene;
  camera: FreeCamera;
  cameraState: CameraState;
  points: readonly GalaxySystemPoint[];
  catalogueStars: BabylonGalaxyStarBatch;
  finderStars: BabylonGalaxyStarBatch;
  starVertexPositions: Float32Array;
  stellarAccentMeshes: readonly Mesh[];
  densityMesh: Mesh | null;
  commanderHistoryMesh: ReturnType<typeof CreateSphere> | null;
  nebulaMesh: Mesh | null;
  nebulaCloudMesh: Mesh | null;
  planetaryNebulaMesh: ReturnType<typeof CreateSphere> | null;
  nebulaeMetadata: ReturnType<typeof galaxyNebulaeMetadata> | null;
  regionBoundaryMesh: ReturnType<typeof CreateLineSystem> | null;
  regionFillMeshes: readonly GalaxyRegionFillMesh[];
  regionLookup: GalaxyRegionMapSource | null;
  referenceGrid: BabylonGalaxyReferenceGrid;
  visualGlow: GlowLayer;
  selectedMarker: ReturnType<typeof CreateTorus> | null;
  selectedSystemId64: string | null;
  selectedRegionId: number | null;
  markerSizeLy: number;
  renderedLayers: readonly RenderedLayerReceipt[];
}>;

export type BabylonSystemBodyMesh = Readonly<{
  layout: SystemBodyLayout;
  mesh: Mesh;
  ringMeshes: readonly ReturnType<typeof CreateTorus>[];
}>;

export type BabylonSystemProduct = Readonly<{
  scene: Scene;
  camera: FreeCamera;
  cameraState: SystemCameraState;
  contract: SystemSceneContract;
  bodies: readonly BabylonSystemBodyMesh[];
  orbitMesh: ReturnType<typeof CreateLineSystem> | null;
  parentLinkMesh: ReturnType<typeof CreateLineSystem> | null;
  hoveredBodyId: number | null;
  visualGlow: GlowLayer;
  renderedLayers: readonly RenderedLayerReceipt[];
}>;

export type BabylonGalaxyReferenceGrid = Readonly<{
  spec: GalaxyReferenceGridSpec;
  minorMesh: ReturnType<typeof CreateLineSystem> | null;
  majorMesh: ReturnType<typeof CreateLineSystem> | null;
  axisMesh: ReturnType<typeof CreateLineSystem> | null;
  /** One line material shared by every grid rebuild in this scene. */
  material: Material | null;
}>;

export type GalaxyRegionFillMesh = Readonly<{
  regionId: number;
  regionName: string;
  mesh: Mesh;
  material: StandardMaterial;
}>;

type GalaxySystemsSceneLayer = Readonly<{
  contributionId: string;
  contributionRevision: number;
  layerId: 'finder-systems' | 'catalogue-systems';
  layerVersion: number;
  representation:
    'AUTHORITATIVE' | 'DERIVED' | 'PLANNED' | 'SCHEMATIC' | 'AMBIENT';
  points: readonly GalaxySystemPoint[];
}>;

type CommanderHistorySceneLayer = Readonly<{
  contributionId: string;
  contributionRevision: number;
  layerVersion: number;
  representation: 'DERIVED';
  truncated: boolean;
  payload: CommanderHistoryPayload;
}>;

type GalaxyNebulaeSceneLayer = Readonly<{
  contributionId: string;
  contributionRevision: number;
  layerVersion: number;
  representation: 'AMBIENT';
  payload: GalaxyNebulaePayload;
}>;

function isGalaxySystemsPayload(value: unknown): value is GalaxySystemsPayload {
  if (!value || typeof value !== 'object' || !('systems' in value))
    return false;
  const systems = (value as { systems?: unknown }).systems;
  return (
    Array.isArray(systems) &&
    systems.every((point) => {
      if (!point || typeof point !== 'object') return false;
      const candidate = point as {
        systemId64?: unknown;
        name?: unknown;
        positionLy?: unknown;
      };
      if (
        typeof candidate.systemId64 !== 'string' ||
        typeof candidate.name !== 'string' ||
        !candidate.positionLy ||
        typeof candidate.positionLy !== 'object'
      ) {
        return false;
      }
      const position = candidate.positionLy as {
        x?: unknown;
        y?: unknown;
        z?: unknown;
      };
      return [position.x, position.y, position.z].every(
        (coordinate) =>
          typeof coordinate === 'number' && Number.isFinite(coordinate),
      );
    })
  );
}

function galaxySystemsSceneLayers(
  scene: GalaxySceneContract,
): readonly GalaxySystemsSceneLayer[] {
  const accepted: GalaxySystemsSceneLayer[] = [];
  for (const contribution of scene.contributions) {
    if (contribution.owner !== 'FINDER' && contribution.owner !== 'CATALOGUE')
      continue;
    for (const layer of contribution.layers) {
      const acceptedLayer =
        (contribution.owner === 'FINDER' && layer.id === 'finder-systems') ||
        (contribution.owner === 'CATALOGUE' &&
          layer.id === 'catalogue-systems');
      if (acceptedLayer && isGalaxySystemsPayload(layer.payload)) {
        if (layer.targetCount !== layer.payload.systems.length) {
          throw new Error(
            'Finder system targetCount must equal its accepted point count',
          );
        }
        accepted.push({
          contributionId: contribution.id,
          contributionRevision: contribution.revision,
          layerId: layer.id as 'finder-systems' | 'catalogue-systems',
          layerVersion: layer.version,
          representation: layer.representation,
          points: layer.payload.systems,
        });
      }
    }
  }
  return accepted;
}

function uniqueGalaxySystemPoints(
  layers: readonly GalaxySystemsSceneLayer[],
): readonly GalaxySystemPoint[] {
  const points = new Map<string, GalaxySystemPoint>();
  const finderIds = new Set<string>();
  for (const layer of layers) {
    for (const point of layer.points) {
      // Finder results carry the richer summary, independent of layer order.
      // A reserved flag from any layer is kept on the merged point.
      const finder = layer.layerId === 'finder-systems';
      const existing = points.get(point.systemId64);
      if (!existing || (finder && !finderIds.has(point.systemId64))) {
        points.set(
          point.systemId64,
          existing?.reserved && !point.reserved
            ? { ...point, reserved: true }
            : point,
        );
      } else if (point.reserved && !existing.reserved) {
        points.set(point.systemId64, { ...existing, reserved: true });
      }
      if (finder) finderIds.add(point.systemId64);
    }
  }
  return [...points.values()];
}

function partitionGalaxyStars(
  points: readonly GalaxySystemPoint[],
  layers: readonly GalaxySystemsSceneLayer[],
  selection: GalaxySceneContract['selection'],
): { catalogue: GalaxySystemPoint[]; finder: GalaxySystemPoint[] } {
  // Finder contributions also carry retained selections/reference markers.
  // A selected catalogue target joins the same always-visible batch, as does
  // any reserved application target (pins, watchlist, search reference) so it
  // survives the wide/local hand-off and stays pickable behind density.
  const finderIds = new Set(
    layers.flatMap((layer) =>
      layer.layerId === 'finder-systems'
        ? layer.points.map((point) => point.systemId64)
        : [],
    ),
  );
  for (const target of selection) {
    if (target.kind === 'system') finderIds.add(target.systemId64);
  }
  const catalogue: GalaxySystemPoint[] = [];
  const finder: GalaxySystemPoint[] = [];
  for (const point of points) {
    (finderIds.has(point.systemId64) || point.reserved === true
      ? finder
      : catalogue
    ).push(point);
  }
  return { catalogue, finder };
}

function galaxySystemsReceipts(
  layers: readonly GalaxySystemsSceneLayer[],
  finderPoints: readonly GalaxySystemPoint[],
): RenderedLayerReceipt[] {
  const finderIds = new Set(finderPoints.map((point) => point.systemId64));
  return layers.map((layer) => {
    const finder = layer.points.filter((point) =>
      finderIds.has(point.systemId64),
    ).length;
    return {
      contributionId: layer.contributionId,
      contributionRevision: layer.contributionRevision,
      layerId: layer.layerId,
      layerVersion: layer.layerVersion,
      representation: layer.representation,
      acceptedTargetCount: layer.points.length,
      renderedTargetCount: layer.points.length,
      renderedBatchCounts: { catalogue: layer.points.length - finder, finder },
    };
  });
}

function isCommanderHistoryPayload(
  value: unknown,
): value is CommanderHistoryPayload {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<CommanderHistoryPayload>;
  return (
    candidate.source === 'journal-log' &&
    (candidate.mode === 'markers' || candidate.mode === 'density') &&
    Array.isArray(candidate.points) &&
    candidate.points.every((point) => {
      if (!point || typeof point !== 'object') return false;
      const visit = point as Partial<CommanderVisitPoint>;
      return (
        !!visit.positionLy &&
        [visit.positionLy.x, visit.positionLy.y, visit.positionLy.z].every(
          (coordinate) =>
            typeof coordinate === 'number' && Number.isFinite(coordinate),
        ) &&
        Number.isSafeInteger(visit.visitCount) &&
        (visit.visitCount ?? 0) > 0 &&
        typeof visit.firstVisitedAt === 'string' &&
        typeof visit.lastVisitedAt === 'string'
      );
    })
  );
}

function commanderHistorySceneLayer(
  scene: GalaxySceneContract,
): CommanderHistorySceneLayer | null {
  for (const contribution of scene.contributions) {
    if (contribution.owner !== 'COMMANDER_HISTORY') continue;
    for (const layer of contribution.layers) {
      if (
        layer.id === COMMANDER_HISTORY_LAYER_ID &&
        layer.representation === 'DERIVED' &&
        isCommanderHistoryPayload(layer.payload)
      ) {
        if (layer.targetCount !== layer.payload.points.length) {
          throw new Error(
            'Commander history targetCount must equal its accepted point count',
          );
        }
        return {
          contributionId: contribution.id,
          contributionRevision: contribution.revision,
          layerVersion: layer.version,
          representation: 'DERIVED',
          truncated: layer.truncated,
          payload: layer.payload,
        };
      }
    }
  }
  return null;
}

function isGalaxyNebulaePayload(value: unknown): value is GalaxyNebulaePayload {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<GalaxyNebulaePayload>;
  return (
    typeof candidate.sourceUrl === 'string' &&
    typeof candidate.attribution === 'string' &&
    typeof candidate.rightsNotice === 'string' &&
    typeof candidate.usageBasis === 'string' &&
    typeof candidate.sourceByteCount === 'number' &&
    typeof candidate.sourceSha256 === 'string' &&
    Array.isArray(candidate.nebulae) &&
    candidate.nebulae.every(
      (nebula) =>
        typeof nebula.id === 'string' &&
        (nebula.kind === 'nebula' || nebula.kind === 'planetary-nebula') &&
        nebula.positionLy.representation === 'AUTHORITATIVE' &&
        [
          nebula.positionLy.value.x,
          nebula.positionLy.value.y,
          nebula.positionLy.value.z,
        ].every(Number.isFinite),
    )
  );
}

function galaxyNebulaeSceneLayer(
  scene: GalaxySceneContract,
): GalaxyNebulaeSceneLayer | null {
  for (const contribution of scene.contributions) {
    if (contribution.owner !== 'CATALOGUE') continue;
    for (const layer of contribution.layers) {
      if (
        layer.id === GALAXY_NEBULAE_LAYER_ID &&
        layer.representation === 'AMBIENT' &&
        isGalaxyNebulaePayload(layer.payload)
      ) {
        if (layer.targetCount !== layer.payload.nebulae.length) {
          throw new Error(
            'Galaxy nebula targetCount must equal its accepted landmark count',
          );
        }
        return {
          contributionId: contribution.id,
          contributionRevision: contribution.revision,
          layerVersion: layer.version,
          representation: 'AMBIENT',
          payload: layer.payload,
        };
      }
    }
  }
  return null;
}

export function applyGalaxyCamera(
  camera: FreeCamera,
  state: CameraState,
): void {
  const pose = galaxyCameraPose(state);
  const distance = state.distanceLy;
  camera.position.set(pose.positionLy.x, pose.positionLy.y, pose.positionLy.z);
  camera.upVector.set(pose.up.x, pose.up.y, pose.up.z);
  // TargetCamera.setTarget nudges an exact pole and drops roll. Construct the
  // orientation from our explicit basis to preserve top-down bearing and LY.
  const forward = new Vector3(pose.targetLy.x, pose.targetLy.y, pose.targetLy.z)
    .subtract(camera.position)
    .normalize();
  camera.rotationQuaternion = Quaternion.FromRotationMatrix(
    Matrix.LookAtLH(Vector3.Zero(), forward, camera.upVector).invert(),
  );
  camera.updateUpVectorFromRotation = true;
  camera.fov = GALAXY_CAMERA_FOV_RAD;
  camera.mode =
    state.projection === 'orthographic'
      ? Camera.ORTHOGRAPHIC_CAMERA
      : Camera.PERSPECTIVE_CAMERA;
  const engine = camera.getEngine();
  const halfHeight = distance * Math.tan(camera.fov / 2);
  const halfWidth =
    (halfHeight * engine.getRenderWidth()) /
    Math.max(1, engine.getRenderHeight());
  camera.orthoLeft = -halfWidth;
  camera.orthoRight = halfWidth;
  camera.orthoBottom = -halfHeight;
  camera.orthoTop = halfHeight;
  camera.minZ = Math.max(0.01, distance / 100_000);
  camera.maxZ = Math.max(10_000, distance * 8);
}

function createFadedGridLines(
  scene: Scene,
  name: string,
  lines: readonly GalaxyGridLine[],
  colour: Color3,
  centreAlpha: number,
  material: Material | null,
): ReturnType<typeof CreateLineSystem> | null {
  if (lines.length === 0) return null;
  const edgeAlpha = centreAlpha * 0.12;
  const mesh = CreateLineSystem(
    name,
    {
      lines: lines.map((line) =>
        line.map((point) => new Vector3(point.x, point.y, point.z)),
      ),
      colors: lines.map(() => [
        new Color4(colour.r, colour.g, colour.b, edgeAlpha),
        new Color4(colour.r, colour.g, colour.b, centreAlpha),
        new Color4(colour.r, colour.g, colour.b, edgeAlpha),
      ]),
      updatable: false,
      ...(material ? { material } : {}),
    },
    scene,
  );
  mesh.isPickable = false;
  mesh.renderingGroupId = 0;
  return mesh;
}

/**
 * The grid is rebuilt whenever zoom or pan changes its spacing. Every rebuild
 * reuses one line material: a fresh material per rebuild released its shader
 * and compiled and linked it again on each zoom step, which stalls the frame.
 */
function createGalaxyReferenceGrid(
  scene: Scene,
  camera: CameraState,
  sharedMaterial: Material | null = null,
): BabylonGalaxyReferenceGrid {
  const engine = scene.getEngine();
  const spec = galaxyReferenceGrid(camera, {
    width: Math.max(1, engine.getRenderWidth()),
    height: Math.max(1, engine.getRenderHeight()),
  });
  let material = sharedMaterial;
  const lines = (
    name: string,
    source: readonly GalaxyGridLine[],
    colour: Color3,
    centreAlpha: number,
  ) => {
    const mesh = createFadedGridLines(
      scene,
      name,
      source,
      colour,
      centreAlpha,
      material,
    );
    material ??= mesh?.material ?? null;
    return mesh;
  };
  const minorMesh = lines(
    'galaxy-reference-grid-minor',
    spec.minorLines,
    new Color3(0.045, 0.2, 0.27),
    0.12,
  );
  const majorMesh = lines(
    'galaxy-reference-grid-major',
    spec.majorLines,
    new Color3(0.08, 0.37, 0.48),
    0.25,
  );
  const axisMesh = lines(
    'galaxy-reference-grid-axes',
    spec.axisLines,
    new Color3(0.78, 0.45, 0.1),
    0.46,
  );
  const metadata = {
    spatialLayer: {
      id: 'galaxy-reference-grid',
      representation: 'SCHEMATIC',
      nominalPlaneY: spec.nominalPlaneY,
      renderPlaneY: spec.renderPlaneY,
      minorStepLy: spec.minorStepLy,
      majorStepLy: spec.majorStepLy,
      cameraDependentGeometry: true,
      affectsSystemPositions: false,
    },
  };
  if (minorMesh) minorMesh.metadata = metadata;
  if (majorMesh) majorMesh.metadata = metadata;
  if (axisMesh) axisMesh.metadata = metadata;
  return { spec, minorMesh, majorMesh, axisMesh, material };
}

/** Disposes the grid lines but keeps the shared material for the next grid. */
function disposeGalaxyReferenceGridLines(
  grid: BabylonGalaxyReferenceGrid,
): void {
  grid.minorMesh?.dispose(false, false, true);
  grid.majorMesh?.dispose(false, false, true);
  grid.axisMesh?.dispose(false, false, true);
}

function refreshGalaxyReferenceGrid(
  product: BabylonGalaxyProduct,
  camera: CameraState,
): BabylonGalaxyReferenceGrid {
  const candidate = galaxyReferenceGrid(camera, {
    width: Math.max(1, product.scene.getEngine().getRenderWidth()),
    height: Math.max(1, product.scene.getEngine().getRenderHeight()),
  });
  if (candidate.key === product.referenceGrid.spec.key) {
    return product.referenceGrid;
  }
  const next = createGalaxyReferenceGrid(
    product.scene,
    camera,
    product.referenceGrid.material,
  );
  disposeGalaxyReferenceGridLines(product.referenceGrid);
  return next;
}

function setStarBatchAlpha(batch: BabylonGalaxyStarBatch, alpha: number): void {
  (batch.mesh.material as StandardMaterial).alpha = alpha;
  // The glow layer must fade stellar accents with their owning markers.
  for (const accent of batch.accentMeshes) {
    (accent.material as StandardMaterial).alpha = alpha;
  }
  batch.mesh.isPickable = alpha > 1e-3;
  batch.mesh.thinInstanceEnablePicking = batch.mesh.isPickable;
}

// Reapply on camera changes and scene/density builds, before their first frame.
// Without factual density, catalogue stars still carry the full view.
function applyDensityCrossfade(product: BabylonGalaxyProduct): void {
  const crossfadeT = product.densityMesh?.material
    ? densityCrossfadeT(
        product.cameraState.distanceLy,
        DENSITY_CROSSFADE_NEAR_LY,
        DENSITY_CROSSFADE_FAR_LY,
      )
    : 0;
  if (product.densityMesh?.material) {
    // Preserve the density material's 0.9 maximum opacity.
    (product.densityMesh.material as StandardMaterial).alpha = 0.9 * crossfadeT;
  }
  setStarBatchAlpha(product.catalogueStars, 1 - crossfadeT);
  setStarBatchAlpha(product.finderStars, 1);
}

function interactiveStarBatch(batch: BabylonGalaxyStarBatch): boolean {
  return (
    batch.mesh.isEnabled() &&
    batch.mesh.isPickable &&
    (batch.mesh.material?.alpha ?? 1) > 1e-3
  );
}

function createGalaxyRegionBoundaryMesh(
  scene: Scene,
  regions: GalaxyRegionsSceneLayer | null,
): ReturnType<typeof CreateLineSystem> | null {
  if (!regions || regions.payload.boundaries.length === 0) return null;
  const mesh = CreateLineSystem(
    'authoritative-galaxy-region-boundaries',
    {
      lines: regions.payload.boundaries.map((boundary) => [
        new Vector3(
          boundary.sourceLy.x,
          boundary.sourceLy.y,
          boundary.sourceLy.z,
        ),
        new Vector3(
          boundary.targetLy.x,
          boundary.targetLy.y,
          boundary.targetLy.z,
        ),
      ]),
      updatable: false,
    },
    scene,
  );
  mesh.color = new Color3(0.16, 0.62, 0.88);
  mesh.alpha = 0.46;
  mesh.isPickable = false;
  mesh.renderingGroupId = 1;
  mesh.metadata = {
    spatialLayer: {
      id: 'galaxy-regions',
      representation: 'AUTHORITATIVE',
      sourceSha256: regions.payload.source.sha256,
      regionCount: regions.payload.regions.length,
      boundaryCount: regions.payload.boundaries.length,
      lookupWidth: regions.payload.source.width,
      lookupHeight: regions.payload.source.height,
      pickableRegionCount: regions.payload.regions.length,
    },
  };
  return mesh;
}

function regionBaseColor(regionId: number): Color3 {
  const tones = [
    new Color3(0.045, 0.19, 0.28),
    new Color3(0.075, 0.14, 0.28),
    new Color3(0.035, 0.22, 0.2),
    new Color3(0.11, 0.14, 0.27),
  ] as const;
  return tones[regionId % tones.length]!.clone();
}

function regionSelectedColor(): Color3 {
  return new Color3(0.92, 0.55, 0.12);
}

function updateGalaxyReferenceGridVisibility(
  grid: BabylonGalaxyReferenceGrid,
  camera: CameraState,
): void {
  // The galactic-plane grid is a zoom-in reference: fully hidden at galaxy
  // scale and faded in only as the camera closes on a neighbourhood, so it
  // never rails across the whole star field.
  const fadeStartLy = 18_000;
  const fadeEndLy = 8_000;
  const visibility = Math.max(
    0,
    Math.min(1, (fadeStartLy - camera.distanceLy) / (fadeStartLy - fadeEndLy)),
  );
  const visible = visibility > 0;
  for (const mesh of [grid.minorMesh, grid.majorMesh, grid.axisMesh]) {
    if (!mesh) continue;
    mesh.setEnabled(visible);
    mesh.visibility = visibility;
  }
}

function selectedGalaxyRegionId(contract: GalaxySceneContract): number | null {
  const target = contract.selection.find(
    (candidate) => candidate.kind === 'region',
  );
  if (!target || target.kind !== 'region') return null;
  const regionId = Number(target.id);
  return Number.isSafeInteger(regionId) && regionId >= 1 && regionId <= 42
    ? regionId
    : null;
}

function selectedGalaxySystemId64(
  contract: GalaxySceneContract,
): string | null {
  const target = contract.selection.find(
    (candidate) => candidate.kind === 'system',
  );
  return target?.kind === 'system' ? target.systemId64 : null;
}

function createGalaxyRegionFillMeshes(
  scene: Scene,
  regions: GalaxyRegionsSceneLayer | null,
  selectedRegionId: number | null,
): readonly GalaxyRegionFillMesh[] {
  if (!regions) return [];
  return buildGalaxyRegionFillGeometry(regions.payload.lookup).map(
    (geometry) => {
      const { id: regionId, name: regionName } = geometry.region;
      const mesh = new Mesh(
        `authoritative-galaxy-region-fill-${regionId}`,
        scene,
      );
      const vertexData = new VertexData();
      vertexData.positions = geometry.positions;
      vertexData.indices = geometry.indices;
      vertexData.applyToMesh(mesh, false);
      mesh.isPickable = true;
      mesh.renderingGroupId = 0;
      mesh.alphaIndex = 0;
      mesh.metadata = {
        spatialLayer: {
          id: 'galaxy-regions',
          representation: 'AUTHORITATIVE',
          regionId,
          regionName,
          rectangleCount: geometry.rectangleCount,
          sourceCellCount: geometry.sourceCellCount,
        },
      };

      const material = new StandardMaterial(
        `authoritative-galaxy-region-fill-material-${regionId}`,
        scene,
      );
      material.disableLighting = true;
      material.backFaceCulling = false;
      material.disableDepthWrite = true;
      material.diffuseColor = regionBaseColor(regionId);
      material.emissiveColor = regionBaseColor(regionId);
      material.alpha =
        selectedRegionId === regionId
          ? REGION_SELECTED_ALPHA
          : REGION_BASE_ALPHA;
      if (selectedRegionId === regionId) {
        material.diffuseColor.copyFrom(regionSelectedColor());
        material.emissiveColor.copyFrom(regionSelectedColor());
      }
      mesh.material = material;
      return { regionId, regionName, mesh, material };
    },
  );
}

export function updateGalaxyRegionFillAppearance(
  product: BabylonGalaxyProduct,
  hoveredRegionId: number | null,
): void {
  for (const fill of product.regionFillMeshes) {
    const selected = fill.regionId === product.selectedRegionId;
    const hovered = fill.regionId === hoveredRegionId;
    if (selected) {
      fill.material.diffuseColor.copyFrom(regionSelectedColor());
      fill.material.emissiveColor.copyFrom(regionSelectedColor());
      fill.material.alpha = REGION_SELECTED_ALPHA;
    } else if (hovered) {
      fill.material.diffuseColor.set(0.035, 0.56, 0.82);
      fill.material.emissiveColor.set(0.09, 0.78, 1);
      fill.material.alpha = REGION_HOVERED_ALPHA;
    } else {
      const base = regionBaseColor(fill.regionId);
      fill.material.diffuseColor.copyFrom(base);
      fill.material.emissiveColor.copyFrom(base);
      fill.material.alpha = REGION_BASE_ALPHA;
    }
  }
}

function targetKey(target: SpatialTarget | undefined): string {
  if (!target) return '';
  switch (target.kind) {
    case 'system':
      return `system:${target.systemId64}`;
    case 'body':
      return `body:${target.ref.systemId64}:${target.ref.bodyId}`;
    case 'facility':
      return `facility:${target.ref.owner}:${target.ref.facilityId}`;
    default:
      return `${target.kind}:${target.id}`;
  }
}

function sameGalaxyRenderInputs(
  current: GalaxySceneContract,
  next: GalaxySceneContract,
): boolean {
  const currentCamera = current.camera;
  const nextCamera = next.camera;
  if (
    currentCamera.revision !== nextCamera.revision ||
    currentCamera.distanceLy !== nextCamera.distanceLy ||
    currentCamera.bearingRad !== nextCamera.bearingRad ||
    currentCamera.pitchRad !== nextCamera.pitchRad ||
    currentCamera.projection !== nextCamera.projection ||
    currentCamera.focusLy.x !== nextCamera.focusLy.x ||
    currentCamera.focusLy.y !== nextCamera.focusLy.y ||
    currentCamera.focusLy.z !== nextCamera.focusLy.z ||
    current.contributions.length !== next.contributions.length
  ) {
    return false;
  }

  return current.contributions.every((contribution, contributionIndex) => {
    const nextContribution = next.contributions[contributionIndex];
    return (
      nextContribution?.id === contribution.id &&
      nextContribution.owner === contribution.owner &&
      nextContribution.revision === contribution.revision &&
      nextContribution.layers.length === contribution.layers.length &&
      contribution.layers.every((layer, layerIndex) => {
        const nextLayer = nextContribution.layers[layerIndex];
        return (
          nextLayer?.id === layer.id &&
          nextLayer.version === layer.version &&
          nextLayer.representation === layer.representation &&
          nextLayer.targetCount === layer.targetCount &&
          nextLayer.truncated === layer.truncated &&
          nextLayer.payload === layer.payload
        );
      })
    );
  });
}

function canPatchGalaxySelection(
  current: GalaxySceneContract,
  next: GalaxySceneContract,
): boolean {
  return sameGalaxyRenderInputs(current, next);
}

function createSelectedSystemMarker(
  scene: Scene,
  point: GalaxySystemPoint,
  markerSizeLy: number,
): ReturnType<typeof CreateTorus> {
  const marker = CreateTorus(
    'selected-system-marker',
    {
      diameter: markerSizeLy * 5.8,
      thickness: markerSizeLy * 0.3,
      tessellation: 32,
    },
    scene,
  );
  marker.position.set(
    point.positionLy.x,
    point.positionLy.y,
    point.positionLy.z,
  );
  marker.rotation.x = Math.PI / 2;
  const material = new StandardMaterial('selected-system-material', scene);
  material.disableLighting = true;
  material.emissiveColor = new Color3(1, 0.58, 0.08);
  marker.material = material;
  return marker;
}

function catalogueDensityReceipt(
  density: CatalogueDensitySceneLayer,
): RenderedLayerReceipt {
  return {
    contributionId: density.contributionId,
    contributionRevision: density.contributionRevision,
    layerId: 'catalogue-density',
    layerVersion: density.layerVersion,
    representation: 'DERIVED',
    acceptedTargetCount: density.payload.cells.length,
    renderedTargetCount: density.payload.cells.length,
    sourceGeneration: density.payload.generationId,
  };
}

export function commanderHistoryHeatColour(
  visitCount: number,
  maximumVisitCount: number,
): readonly [number, number, number, number] {
  if (!Number.isSafeInteger(visitCount) || visitCount <= 0)
    throw new RangeError(
      'Commander history colour requires a positive visit count',
    );
  if (!Number.isSafeInteger(maximumVisitCount) || maximumVisitCount <= 0)
    throw new RangeError(
      'Commander history colour requires a positive maximum',
    );
  const intensity = Math.min(
    1,
    Math.log1p(visitCount) / Math.log1p(maximumVisitCount),
  );
  if (intensity < 0.5) {
    const local = intensity * 2;
    return [0.08, 0.28 + local * 0.58, 1 - local * 0.18, 0.38 + local * 0.28];
  }
  const local = (intensity - 0.5) * 2;
  return [0.08 + local * 0.92, 0.86, 0.82 - local * 0.68, 0.66 + local * 0.26];
}

function createCommanderHistoryMesh(
  scene: Scene,
  history: CommanderHistorySceneLayer | null,
  markerSizeLy: number,
): ReturnType<typeof CreateSphere> | null {
  if (!history?.payload.points.length) return null;
  const mesh = CreateSphere(
    'commander-history-heatmap',
    { diameter: 2, segments: 8 },
    scene,
  );
  mesh.isPickable = false;
  mesh.thinInstanceEnablePicking = false;
  mesh.hasVertexAlpha = true;
  mesh.alphaIndex = 2;
  const material = new StandardMaterial('commander-history-material', scene);
  material.disableLighting = true;
  material.diffuseColor = Color3.White();
  material.emissiveColor = new Color3(0.68, 0.68, 0.68);
  material.specularColor = Color3.Black();
  material.alpha = 0.82;
  mesh.material = material;
  mesh.onDisposeObservable.addOnce(() => material.dispose());

  const maximumVisitCount = history.payload.points.reduce(
    (maximum, point) => Math.max(maximum, point.visitCount),
    1,
  );
  const matrices = new Float32Array(history.payload.points.length * 16);
  const colours = new Float32Array(history.payload.points.length * 4);
  history.payload.points.forEach((point, index) => {
    const radius = point.cellSizeLy
      ? Math.max(markerSizeLy, point.cellSizeLy * 0.42)
      : markerSizeLy *
        (1.5 + Math.min(1.2, Math.log1p(point.visitCount) * 0.25));
    Matrix.Compose(
      new Vector3(radius, radius, radius),
      Quaternion.Identity(),
      new Vector3(point.positionLy.x, point.positionLy.y, point.positionLy.z),
    ).copyToArray(matrices, index * 16);
    colours.set(
      commanderHistoryHeatColour(point.visitCount, maximumVisitCount),
      index * 4,
    );
  });
  mesh.thinInstanceSetBuffer('matrix', matrices, 16, true);
  mesh.thinInstanceSetBuffer('color', colours, 4, true);
  mesh.thinInstanceRefreshBoundingInfo(true);
  mesh.metadata = {
    spatialLayer: {
      id: COMMANDER_HISTORY_LAYER_ID,
      representation: 'DERIVED',
      owner: 'COMMANDER_HISTORY',
      source: 'journal-log',
      mode: history.payload.mode,
      renderedPointCount: history.payload.points.length,
      truncated: history.truncated,
      catalogueDensity: false,
    },
  };
  return mesh;
}

function commanderHistoryReceipt(
  history: CommanderHistorySceneLayer,
): RenderedLayerReceipt {
  return {
    contributionId: history.contributionId,
    contributionRevision: history.contributionRevision,
    layerId: COMMANDER_HISTORY_LAYER_ID,
    layerVersion: history.layerVersion,
    representation: history.representation,
    acceptedTargetCount: history.payload.points.length,
    renderedTargetCount: history.payload.points.length,
  };
}

function createPlanetaryNebulaeMesh(
  scene: Scene,
  landmarks: readonly GalaxyNebula[],
): Mesh | null {
  if (!landmarks.length) return null;
  const mesh = CreateSphere(
    'galaxy-planetary-nebula-markers',
    { diameter: PLANETARY_NEBULA_MARKER_RADIUS_LY * 2, segments: 6 },
    scene,
  );
  mesh.isPickable = false;
  mesh.thinInstanceEnablePicking = false;
  const material = new StandardMaterial('planetary-nebula-material', scene);
  material.disableLighting = true;
  material.disableDepthWrite = true;
  material.diffuseColor = Color3.Black();
  material.emissiveColor = new Color3(0.3, 0.5, 0.55);
  material.specularColor = Color3.Black();
  material.alpha = 0.3;
  mesh.material = material;
  mesh.onDisposeObservable.addOnce(() => material.dispose());
  const matrices = new Float32Array(landmarks.length * 16);
  landmarks.forEach((nebula, index) => {
    const { x, y, z } = nebula.positionLy.value;
    Matrix.Translation(x, y, z).copyToArray(matrices, index * 16);
  });
  mesh.thinInstanceSetBuffer('matrix', matrices, 16, true);
  mesh.thinInstanceRefreshBoundingInfo(true);
  return mesh;
}

function galaxyNebulaeMetadata(
  nebulae: GalaxyNebulaeSceneLayer,
  selection: GalaxyNebulaLandmarks,
) {
  const common = {
    id: GALAXY_NEBULAE_LAYER_ID,
    representation: 'AMBIENT',
    owner: 'CATALOGUE',
    coordinateRepresentation: 'AUTHORITATIVE',
    radiusMeaning: 'schematic-map-presentation-not-physical-extent',
    sourceUrl: nebulae.payload.sourceUrl,
    attribution: nebulae.payload.attribution,
    rightsNotice: nebulae.payload.rightsNotice,
    usageBasis: nebulae.payload.usageBasis,
    sourceByteCount: nebulae.payload.sourceByteCount,
    sourceSha256: nebulae.payload.sourceSha256,
    // Target counts describe unique landmarks with enabled markers at this
    // zoom, not pixel coverage/frustum/occlusion. Decorative cloud quads never
    // add targets to M02 receipts or the inventory/status line.
    visibilityMeaning: 'scene-instances-enabled-at-current-zoom',
    targetCountMeaning: 'unique-enabled-landmark-markers-excluding-cloud-quads',
  };
  const kindMetadata = (kind: NebulaKind) => ({
    ...common,
    kind,
    ...selection.counts[kind],
    selectedLandmarkIds: selection.landmarks[kind].map((nebula) => nebula.id),
    currentlyVisibleCount: 0,
    renderedPointCount: 0,
  });
  const countsByKind = {
    nebula: {
      ...kindMetadata('nebula'),
      landmarkCellLy: NEBULA_LANDMARK_CELL_LY,
      landmarkLimit: NEBULA_LANDMARK_LIMIT,
      selectionRule: 'named-then-regional-then-lowest-codepoint-id',
      markerRadiusLy: NEBULA_LANDMARK_MARKER_RADIUS_LY,
      markerScreenDiameterPx: [3, 6],
      markerVisibility: 'persistent-at-all-galaxy-zooms',
      extentMeaning: 'schematic extent; catalogue supplies positions only',
      schematicRadiusMinLy: NEBULA_SCHEMATIC_RADIUS_MIN_LY,
      schematicRadiusMaxLy: NEBULA_SCHEMATIC_RADIUS_MAX_LY,
      clouds: {
        id: GALAXY_NEBULAE_LAYER_ID,
        kind: 'nebula-cloud',
        representation: 'AMBIENT',
        selectedLandmarkCount: selection.counts.nebula.selectedLandmarkCount,
        quadsPerLandmark: NEBULA_CLOUD_QUADS_PER_LANDMARK,
        instanceCount:
          selection.counts.nebula.selectedLandmarkCount *
          NEBULA_CLOUD_QUADS_PER_LANDMARK,
        enabledLandmarkCount: 0,
        enabledQuadCount: 0,
        visibilityMeaning:
          'enabled-decoration-batch-before-camera-proximity-frustum-and-occlusion',
        targetCountMeaning: 'decoration-excluded-from-target-counts',
        extentMeaning: 'schematic extent; catalogue supplies positions only',
        schematicRadiusMinLy: NEBULA_SCHEMATIC_RADIUS_MIN_LY,
        schematicRadiusMaxLy: NEBULA_SCHEMATIC_RADIUS_MAX_LY,
        fadeStartLy: NEBULA_LANDMARK_FADE_START_LY,
        fadeEndLy: NEBULA_LANDMARK_FADE_END_LY,
        reenterLy: NEBULA_CLOUD_REENTER_LY,
      },
    },
    'planetary-nebula': {
      ...kindMetadata('planetary-nebula'),
      landmarkCellLy: null,
      landmarkLimit: null,
      markerRadiusLy: PLANETARY_NEBULA_MARKER_RADIUS_LY,
      fadeStartLy: PLANETARY_NEBULA_FADE_START_LY,
      fadeEndLy: PLANETARY_NEBULA_FADE_END_LY,
    },
  };
  const diffuse = selection.counts.nebula;
  const planetary = selection.counts['planetary-nebula'];
  return {
    ...common,
    countsByKind,
    // Reused by camera updates so the nebula frame path allocates no scratch
    // objects. Diffuse opacity belongs to clouds; markers always stay visible.
    zoomOpacityByKind: { nebula: 0, 'planetary-nebula': 0 },
    catalogueRecordCount:
      diffuse.catalogueRecordCount + planetary.catalogueRecordCount,
    acceptedFeatureCount:
      diffuse.acceptedFeatureCount + planetary.acceptedFeatureCount,
    selectedLandmarkCount:
      diffuse.selectedLandmarkCount + planetary.selectedLandmarkCount,
    deduplicatedEntryCount:
      diffuse.deduplicatedEntryCount + planetary.deduplicatedEntryCount,
    cappedEntryCount: diffuse.cappedEntryCount + planetary.cappedEntryCount,
    currentlyVisibleCount: 0,
    renderedPointCount: 0,
  };
}

function updateGalaxyNebulaVisibility(
  nebulaMesh: Mesh | null,
  nebulaCloudMesh: Mesh | null,
  planetaryNebulaMesh: Mesh | null,
  metadata: ReturnType<typeof galaxyNebulaeMetadata> | null,
  camera: CameraState,
): void {
  if (!metadata) return;
  const visibility = galaxyNebulaVisibility(
    camera.distanceLy,
    metadata.zoomOpacityByKind,
  );
  if (nebulaMesh) {
    nebulaMesh.setEnabled(true);
    nebulaMesh.visibility = 1;
  }
  const diffuse = metadata.countsByKind.nebula;
  diffuse.currentlyVisibleCount = nebulaMesh?.thinInstanceCount ?? 0;
  diffuse.renderedPointCount = diffuse.currentlyVisibleCount;
  if (nebulaCloudMesh) {
    const enabled = nebulaCloudEnabled(
      camera.distanceLy,
      nebulaCloudMesh.isEnabled(),
    );
    nebulaCloudMesh.setEnabled(enabled);
    nebulaCloudMesh.visibility = enabled ? visibility.nebula : 0;
  }
  const clouds = diffuse.clouds;
  clouds.enabledQuadCount =
    nebulaCloudMesh?.isEnabled() && nebulaCloudMesh.visibility > 0
      ? nebulaCloudMesh.thinInstanceCount
      : 0;
  clouds.enabledLandmarkCount =
    clouds.enabledQuadCount / NEBULA_CLOUD_QUADS_PER_LANDMARK;
  if (planetaryNebulaMesh) {
    planetaryNebulaMesh.setEnabled(visibility['planetary-nebula'] > 0);
    planetaryNebulaMesh.visibility = visibility['planetary-nebula'];
  }
  const planetary = metadata.countsByKind['planetary-nebula'];
  planetary.currentlyVisibleCount =
    planetaryNebulaMesh?.isEnabled() && planetaryNebulaMesh.visibility > 0
      ? planetaryNebulaMesh.thinInstanceCount
      : 0;
  planetary.renderedPointCount = planetary.currentlyVisibleCount;
  metadata.currentlyVisibleCount =
    metadata.countsByKind.nebula.currentlyVisibleCount +
    metadata.countsByKind['planetary-nebula'].currentlyVisibleCount;
  metadata.renderedPointCount = metadata.currentlyVisibleCount;
}

function galaxyNebulaeReceipt(
  nebulae: GalaxyNebulaeSceneLayer,
  metadata: ReturnType<typeof galaxyNebulaeMetadata>,
): RenderedLayerReceipt {
  return {
    contributionId: nebulae.contributionId,
    contributionRevision: nebulae.contributionRevision,
    layerId: GALAXY_NEBULAE_LAYER_ID,
    layerVersion: nebulae.layerVersion,
    representation: nebulae.representation,
    acceptedTargetCount: nebulae.payload.nebulae.length,
    renderedTargetCount: metadata.currentlyVisibleCount,
    sourceGeneration: nebulae.payload.sourceLastModified ?? undefined,
  };
}

function replacesCatalogueDensity(
  current: CatalogueDensitySceneLayer | null,
  contribution: SpatialContribution,
): boolean {
  if (contribution.owner !== 'CATALOGUE') return false;
  if (
    contribution.layers.length > 1 ||
    contribution.layers.some((layer) => layer.id !== 'catalogue-density')
  ) {
    return false;
  }
  return current
    ? current.contributionId === contribution.id
    : contribution.layers.length === 1;
}

function createGalaxyVisualFinish(
  scene: Scene,
  glowMeshes: readonly Mesh[],
): GlowLayer {
  const image = scene.imageProcessingConfiguration;
  image.toneMappingEnabled = true;
  image.toneMappingType = ImageProcessingConfiguration.TONEMAPPING_ACES;
  image.exposure = 1.16;
  image.contrast = 1.18;
  image.vignetteEnabled = true;
  image.vignetteBlendMode = ImageProcessingConfiguration.VIGNETTEMODE_MULTIPLY;
  image.vignetteColor = new Color4(0.006, 0.01, 0.02, 1);
  image.vignetteWeight = 1.35;
  image.vignetteStretch = 0.18;
  image.ditheringEnabled = true;
  image.ditheringIntensity = 0.012;

  const glow = new GlowLayer('galaxy-emissive-glow', scene, {
    mainTextureSamples: Math.max(
      1,
      Math.min(4, scene.getEngine().getCaps().maxMSAASamples),
    ),
  });
  glow.blurKernelSize = 20;
  glow.intensity = 0.42;
  // Babylon treats an empty inclusion list as "every mesh". A permanent
  // invisible guard keeps it non-empty, so removing the last glow mesh (for
  // example the selection marker) never pulls the density layer into bloom.
  const guard = CreateSphere('galaxy-glow-guard', { diameter: 0.001 }, scene);
  guard.isVisible = false;
  guard.isPickable = false;
  for (const mesh of [guard, ...glowMeshes]) glow.addIncludedOnlyMesh(mesh);
  return glow;
}

function nearestStarPointByScreen(
  engine: AbstractEngine,
  product: BabylonGalaxyProduct,
  deviceX: number,
  deviceY: number,
  tolerancePx: number,
): GalaxySystemPoint | undefined {
  const scene = product.scene;
  const transform = scene.getTransformMatrix();
  const viewport = new Viewport(
    0,
    0,
    engine.getRenderWidth(),
    engine.getRenderHeight(),
  );
  const identity = Matrix.Identity();
  const projected = Vector3.Zero();
  let best: GalaxySystemPoint | undefined;
  let bestDistanceSq = tolerancePx * tolerancePx;
  let bestReserved = false;
  const toleranceSq = tolerancePx * tolerancePx;
  // Visit the always-visible batch first and retain it on an equal distance.
  // Reserved application targets (selected, reference, results) win an
  // overlapping catalogue star within the same tolerance.
  for (const batch of [product.finderStars, product.catalogueStars]) {
    if (!interactiveStarBatch(batch)) continue;
    for (const point of batch.points) {
      Vector3.ProjectToRef(
        new Vector3(point.positionLy.x, point.positionLy.y, point.positionLy.z),
        identity,
        transform,
        viewport,
        projected,
      );
      if (projected.z < 0 || projected.z > 1) continue;
      const dx = projected.x - deviceX;
      const dy = projected.y - deviceY;
      const distanceSq = dx * dx + dy * dy;
      if (distanceSq > toleranceSq) continue;
      const reserved = point.reserved === true;
      if (
        (reserved && !bestReserved) ||
        (reserved === bestReserved &&
          (distanceSq < bestDistanceSq ||
            (!best && distanceSq <= bestDistanceSq)))
      ) {
        bestDistanceSq = distanceSq;
        best = point;
        bestReserved = reserved;
      }
    }
  }
  return best;
}

function pickGalaxyTarget(
  engine: AbstractEngine,
  product: BabylonGalaxyProduct,
  screenX: number,
  screenY: number,
  options: Readonly<{
    allowNearestStar?: boolean;
    includeSystemName?: boolean;
  }> = {},
): SpatialTarget | undefined {
  const canvas = engine.getRenderingCanvas();
  const scaleX = canvas?.clientWidth
    ? engine.getRenderWidth() / canvas.clientWidth
    : 1;
  const scaleY = canvas?.clientHeight
    ? engine.getRenderHeight() / canvas.clientHeight
    : 1;
  let nearestHit:
    | {
        distance: number;
        systemId64: string;
        name: string | undefined;
        reserved: boolean;
      }
    | undefined;
  // Separate ray picks make batch tie-breaking independent of scene mesh order.
  // A predicate bypasses Babylon's isPickable gate, so gate each batch here.
  for (const batch of [product.finderStars, product.catalogueStars]) {
    const interactive = interactiveStarBatch(batch);
    const selectedMarker =
      batch.kind === 'finder' ? product.selectedMarker : null;
    if (!interactive && !selectedMarker) continue;
    const hit = product.scene.pick(
      screenX * scaleX,
      screenY * scaleY,
      (mesh) => (interactive && mesh === batch.mesh) || mesh === selectedMarker,
    );
    if (!hit?.hit) continue;
    const point =
      hit.pickedMesh === selectedMarker
        ? batch.points.find(
            (candidate) => candidate.systemId64 === product.selectedSystemId64,
          )
        : hit.pickedMesh === batch.mesh && hit.thinInstanceIndex >= 0
          ? batch.points[hit.thinInstanceIndex]
          : undefined;
    const systemId64 =
      hit.pickedMesh === selectedMarker
        ? product.selectedSystemId64
        : point?.systemId64;
    if (systemId64 && (!nearestHit || hit.distance < nearestHit.distance)) {
      nearestHit = {
        distance: hit.distance,
        systemId64,
        name: point?.name,
        reserved: hit.pickedMesh === selectedMarker || point?.reserved === true,
      };
    }
  }
  // Stars render only a few pixels wide, so an exact ray can narrowly miss.
  // Fall back to the nearest projected star within a small screen tolerance
  // before considering a region plane, keeping individual stars clickable.
  // Known application targets win an overlapping catalogue click within that
  // same tolerance; exact selected markers and reserved hits keep precedence.
  const nearestStar = options.allowNearestStar
    ? nearestStarPointByScreen(
        engine,
        product,
        screenX * scaleX,
        screenY * scaleY,
        12 * Math.max(scaleX, scaleY),
      )
    : undefined;
  const chosen =
    nearestStar?.reserved && !nearestHit?.reserved
      ? { systemId64: nearestStar.systemId64, name: nearestStar.name }
      : (nearestHit ?? nearestStar);
  if (chosen)
    return {
      kind: 'system',
      systemId64: chosen.systemId64,
      ...(options.includeSystemName && chosen.name
        ? { name: chosen.name }
        : {}),
    };
  const hit = product.scene.pick(screenX * scaleX, screenY * scaleY, (mesh) =>
    product.regionFillMeshes.some((fill) => fill.mesh === mesh),
  );
  const fill = product.regionFillMeshes.find(
    (candidate) => candidate.mesh === hit?.pickedMesh,
  );
  if (!fill || !hit?.pickedPoint || !product.regionLookup) return undefined;
  const identity = findGalaxyRegionAt(product.regionLookup, {
    x: hit.pickedPoint.x,
    z: hit.pickedPoint.z,
  });
  return identity?.id === fill.regionId
    ? { kind: 'region', id: String(identity.id) }
    : undefined;
}

function setGalaxyStarInstances(
  starMesh: Mesh,
  points: readonly GalaxySystemPoint[],
): Pick<
  BabylonGalaxyStarBatch,
  'instanceMatrices' | 'instanceColours' | 'stellarPresentations'
> {
  const stellarPresentations = points.map((point) =>
    stellarPresentation(point.primaryStar),
  );
  const matrices = new Float32Array(points.length * 16);
  const colours = new Float32Array(points.length * 4);
  points.forEach((point, index) => {
    const presentation = stellarPresentations[index]!;
    Matrix.Compose(
      new Vector3(
        presentation.relativeScale,
        presentation.relativeScale,
        presentation.relativeScale,
      ),
      Quaternion.Identity(),
      new Vector3(point.positionLy.x, point.positionLy.y, point.positionLy.z),
    ).copyToArray(matrices, index * 16);
    colours.set([...presentation.coreRgb, 1], index * 4);
  });
  starMesh.thinInstanceSetBuffer('matrix', matrices, 16, true);
  starMesh.thinInstanceSetBuffer('color', colours, 4, true);
  starMesh.thinInstanceRefreshBoundingInfo(true);
  starMesh.setEnabled(points.length > 0);
  return {
    instanceMatrices: matrices,
    instanceColours: colours,
    stellarPresentations,
  };
}

function createGalaxyStarBatch(
  scene: Scene,
  kind: BabylonGalaxyStarBatch['kind'],
  points: readonly GalaxySystemPoint[],
  markerSizeLy: number,
): BabylonGalaxyStarBatch {
  const starMesh = CreateSphere(
    kind === 'catalogue'
      ? 'finder-system-instances'
      : 'finder-result-instances',
    { diameter: markerSizeLy * 1.25, segments: 8, updatable: true },
    scene,
  );
  const starMaterial = new StandardMaterial(`${kind}-system-material`, scene);
  starMaterial.disableLighting = true;
  starMaterial.emissiveColor = Color3.White();
  starMaterial.diffuseColor = Color3.White();
  starMaterial.specularColor = Color3.Black();
  starMaterial.alpha = 1;
  starMaterial.transparencyMode = Material.MATERIAL_ALPHABLEND;
  Object.assign(starMaterial, { useVertexColors: true });
  starMesh.material = starMaterial;
  starMesh.hasVertexAlpha = true;
  starMesh.thinInstanceEnablePicking = true;
  starMesh.metadata = {
    spatialLayer: {
      id: kind === 'catalogue' ? 'catalogue-systems' : 'finder-systems',
      batch: kind,
    },
  };
  const instances = setGalaxyStarInstances(starMesh, points);
  const stellarAccentMeshes = points.flatMap((point, index) => {
    const presentation = instances.stellarPresentations[index]!;
    if (!presentation.ring) return [];
    const ring = CreateTorus(
      `stellar-icon-ring-${point.systemId64}`,
      {
        diameter: markerSizeLy * presentation.relativeScale * 2.25,
        thickness: markerSizeLy * 0.18,
        tessellation: 28,
      },
      scene,
    );
    ring.position.set(
      point.positionLy.x,
      point.positionLy.y,
      point.positionLy.z,
    );
    ring.rotation.x = Math.PI / 2;
    ring.isPickable = false;
    const material = new StandardMaterial(
      `stellar-icon-ring-material-${point.systemId64}`,
      scene,
    );
    material.disableLighting = true;
    material.emissiveColor = Color3.FromArray(presentation.haloRgb);
    material.diffuseColor = Color3.Black();
    material.specularColor = Color3.Black();
    ring.material = material;
    ring.onDisposeObservable.addOnce(() => material.dispose());
    ring.metadata = {
      spatialLayer: {
        id: 'finder-systems',
        batch: kind,
        systemId64: point.systemId64,
        stellarGlyph: presentation.glyph,
        presentationOnly: true,
      },
    };
    return [ring];
  });
  return {
    kind,
    mesh: starMesh,
    points,
    ...instances,
    accentMeshes: stellarAccentMeshes,
  };
}

function repartitionGalaxyStarBatch(
  batch: BabylonGalaxyStarBatch,
  points: readonly GalaxySystemPoint[],
  accents: readonly Mesh[],
): BabylonGalaxyStarBatch {
  // Preserve GPU buffers for the common selection-only update among Finder rows.
  if (
    points.length === batch.points.length &&
    points.every((point, index) => point === batch.points[index])
  )
    return batch;
  const ids = new Set(points.map((point) => point.systemId64));
  const accentMeshes = accents.filter((accent) =>
    ids.has(accent.metadata.spatialLayer.systemId64),
  );
  for (const accent of accentMeshes)
    accent.metadata.spatialLayer.batch = batch.kind;
  return {
    ...batch,
    points,
    ...setGalaxyStarInstances(batch.mesh, points),
    accentMeshes,
  };
}

export const createBabylonGalaxyScene = (
  engine: AbstractEngine,
  contract: GalaxySceneContract,
): BabylonGalaxyProduct => {
  const scene = new Scene(engine);
  // Svelte owns input and dispatches explicit picks/camera commands. Babylon's
  // default handlers duplicate picking and focus the hidden canvas on release.
  scene.detachControl();
  scene.clearColor = new Color4(0.008, 0.014, 0.028, 1);
  const systemLayers = galaxySystemsSceneLayers(contract);
  const points = uniqueGalaxySystemPoints(systemLayers);
  const density = catalogueDensitySceneLayer(contract);
  const commanderHistory = commanderHistorySceneLayer(contract);
  const nebulae = galaxyNebulaeSceneLayer(contract);
  const regions = galaxyRegionsSceneLayer(contract);
  const selectedRegionId = selectedGalaxyRegionId(contract);
  const cameraState = normalizeGalaxyCamera(contract.camera);
  const camera = new FreeCamera('galaxy-camera', Vector3.Zero(), scene);
  applyGalaxyCamera(camera, cameraState);
  scene.activeCamera = camera;

  const light = new HemisphericLight(
    'galaxy-fill',
    new Vector3(-0.2, 1, -0.3),
    scene,
  );
  light.intensity = 0.35;

  const referenceGrid = createGalaxyReferenceGrid(scene, cameraState);
  updateGalaxyReferenceGridVisibility(referenceGrid, cameraState);
  const densityMesh = createCatalogueDensityMesh(scene, density);
  const markerSizeLy = galaxyStarMarkerSizeLy(cameraState.distanceLy);
  const commanderHistoryMesh = createCommanderHistoryMesh(
    scene,
    commanderHistory,
    markerSizeLy,
  );
  const nebulaSelection = nebulae
    ? selectGalaxyNebulaLandmarks(nebulae.payload)
    : null;
  const nebulaMesh = createGalaxyNebulaLandmarkMesh(
    scene,
    nebulaSelection?.landmarks.nebula ?? [],
  );
  const nebulaCloudMesh = createGalaxyNebulaCloudMesh(
    scene,
    nebulaSelection?.landmarks.nebula ?? [],
  );
  const planetaryNebulaMesh = createPlanetaryNebulaeMesh(
    scene,
    nebulaSelection?.landmarks['planetary-nebula'] ?? [],
  );
  const nebulaeMetadata =
    nebulae && nebulaSelection
      ? galaxyNebulaeMetadata(nebulae, nebulaSelection)
      : null;
  if (nebulaMesh && nebulaeMetadata)
    nebulaMesh.metadata = { spatialLayer: nebulaeMetadata.countsByKind.nebula };
  if (nebulaCloudMesh && nebulaeMetadata)
    nebulaCloudMesh.metadata = {
      spatialLayer: nebulaeMetadata.countsByKind.nebula.clouds,
    };
  if (planetaryNebulaMesh && nebulaeMetadata)
    planetaryNebulaMesh.metadata = {
      spatialLayer: nebulaeMetadata.countsByKind['planetary-nebula'],
    };
  updateGalaxyNebulaVisibility(
    nebulaMesh,
    nebulaCloudMesh,
    planetaryNebulaMesh,
    nebulaeMetadata,
    cameraState,
  );
  const regionFillMeshes = createGalaxyRegionFillMeshes(
    scene,
    regions,
    selectedRegionId,
  );
  const regionBoundaryMesh = createGalaxyRegionBoundaryMesh(scene, regions);

  const batches = partitionGalaxyStars(
    points,
    systemLayers,
    contract.selection,
  );
  const catalogueStars = createGalaxyStarBatch(
    scene,
    'catalogue',
    batches.catalogue,
    markerSizeLy,
  );
  const finderStars = createGalaxyStarBatch(
    scene,
    'finder',
    batches.finder,
    markerSizeLy,
  );
  const stellarAccentMeshes = [
    ...catalogueStars.accentMeshes,
    ...finderStars.accentMeshes,
  ];
  const starVertexPositions = Float32Array.from(
    catalogueStars.mesh.getVerticesData('position') ?? [],
  );

  const selectedSystemId64 = selectedGalaxySystemId64(contract);
  const selectedPoint = points.find(
    (point) => point.systemId64 === selectedSystemId64,
  );
  const selectedMarker = selectedPoint
    ? createSelectedSystemMarker(scene, selectedPoint, markerSizeLy)
    : null;

  // The density layer is already a soft glow. Blooming it as well renders
  // every splat's full emissive colour into the glow target, which adds a flat
  // grey plateau over the whole disc, so it stays out of the glow layer.
  const visualGlow = createGalaxyVisualFinish(
    scene,
    [commanderHistoryMesh, selectedMarker, ...stellarAccentMeshes].filter(
      (mesh): mesh is Mesh => mesh !== null,
    ),
  );

  const renderedLayers: RenderedLayerReceipt[] = [];
  if (density) {
    renderedLayers.push(catalogueDensityReceipt(density));
  }
  if (regions) {
    renderedLayers.push({
      contributionId: regions.contributionId,
      contributionRevision: regions.contributionRevision,
      layerId: 'galaxy-regions',
      layerVersion: regions.layerVersion,
      representation: 'AUTHORITATIVE',
      acceptedTargetCount: regions.payload.regions.length,
      renderedTargetCount: regions.payload.regions.length,
      sourceGeneration: regions.payload.source.sha256,
    });
  }
  renderedLayers.push(
    ...galaxySystemsReceipts(systemLayers, finderStars.points),
  );
  if (commanderHistory)
    renderedLayers.push(commanderHistoryReceipt(commanderHistory));
  if (nebulae && nebulaeMetadata)
    renderedLayers.push(galaxyNebulaeReceipt(nebulae, nebulaeMetadata));
  scene.metadata = {
    spatialScene: {
      kind: contract.kind,
      revision: contract.revision,
      renderedLayers,
      nebulae: nebulaeMetadata,
      visualProfile: {
        toneMapping: 'ACES',
        emissiveGlow: true,
        adaptiveReferenceGrid: true,
        stellarIconFamilies: true,
        systemPositions: 'canonical-light-years',
        ambientDensitySubstitute: false,
        nebulaCoordinates: 'authoritative-reference-systems',
        nebulaCloudExtents: 'schematic',
      },
    },
  };

  const product: BabylonGalaxyProduct = {
    scene,
    camera,
    cameraState,
    points,
    catalogueStars,
    finderStars,
    starVertexPositions,
    stellarAccentMeshes,
    densityMesh,
    commanderHistoryMesh,
    nebulaMesh,
    nebulaCloudMesh,
    planetaryNebulaMesh,
    nebulaeMetadata,
    regionBoundaryMesh,
    regionFillMeshes,
    regionLookup: regions?.payload.lookup ?? null,
    referenceGrid,
    visualGlow,
    selectedMarker,
    selectedSystemId64,
    selectedRegionId,
    markerSizeLy,
    renderedLayers,
  };
  applyDensityCrossfade(product);
  return product;
};

function systemBodyColour(body: BodyVisualDescriptor): Readonly<{
  diffuse: Color3;
  emissive: Color3;
  darkCore: boolean;
  banded: boolean;
  unknown: boolean;
}> {
  const identity =
    `${body.class?.value ?? ''} ${body.subtype?.value ?? ''}`.toLowerCase();
  if (identity.includes('star') || identity.includes('black hole')) {
    const star = stellarPresentation({
      type: body.class?.value,
      subtype: body.subtype?.value,
    });
    return {
      diffuse: Color3.FromArray(star.coreRgb),
      emissive: Color3.FromArray(star.haloRgb),
      darkCore: star.darkCore,
      banded: false,
      unknown: false,
    };
  }
  if (body.class?.value === 'Belt cluster') {
    return {
      diffuse: new Color3(0.46, 0.42, 0.38),
      emissive: Color3.Black(),
      darkCore: false,
      banded: false,
      unknown: false,
    };
  }
  const bodyFamily = body.bodyFamily ?? { family: 'unknown' };
  const diffuse = Color3.FromArray(bodyFamilyPresentation(bodyFamily).colour);
  const unknown =
    bodyFamily.family === 'unknown' &&
    (body.bodyFamily !== undefined || body.class?.value === 'Planet');
  return {
    diffuse,
    emissive: unknown ? diffuse.scale(0.01) : diffuse.scale(0.055),
    darkCore: false,
    banded:
      bodyFamily.family === 'gas-giant' ||
      bodyFamily.family === 'gas-giant-life' ||
      bodyFamily.family === 'helium-gas-giant' ||
      bodyFamily.family === 'water-giant',
    unknown,
  };
}

/** Illustrative latitude bands; no surface texture or observed weather is implied. */
function applyGiantBands(mesh: Mesh, radius: number): void {
  const positions = mesh.getVerticesData('position');
  if (!positions) return;
  const colours = new Float32Array((positions.length / 3) * 4);
  for (let vertex = 0; vertex < positions.length / 3; vertex += 1) {
    const latitude = Math.asin(
      Math.max(-1, Math.min(1, positions[vertex * 3 + 1]! / radius)),
    );
    const tone = 0.9 + Math.cos(latitude * 12) * 0.1;
    colours.set([tone, tone, tone, 1], vertex * 4);
  }
  mesh.setVerticesData('color', colours);
}

function systemCameraTarget(
  camera: SystemCameraState,
  bodies: readonly BabylonSystemBodyMesh[],
): Vector3 {
  if (camera.focus.kind === 'body') {
    const focusedBodyId = camera.focus.ref.bodyId;
    const selected = bodies.find(
      (body) => body.layout.body.ref.bodyId === focusedBodyId,
    );
    if (selected) return selected.mesh.position.clone();
  }
  return Vector3.Zero();
}

export function applySystemCamera(
  camera: FreeCamera,
  state: SystemCameraState,
  bodies: readonly BabylonSystemBodyMesh[],
): void {
  const target = systemCameraTarget(state, bodies);
  const distance = Number.isFinite(state.semanticDistance)
    ? Math.max(6, Math.min(Number.MAX_SAFE_INTEGER, state.semanticDistance))
    : 38;
  // Large nested groups need a wider fit than Babylon's default far plane.
  // Keep the clip distance finite even for malformed camera commands.
  camera.maxZ = Math.max(10_000, distance * 4);
  const pitch = Math.max(0.08, Math.min(1.42, state.pitchRad));
  const horizontal = Math.cos(pitch) * distance;
  camera.position.set(
    target.x + Math.sin(state.bearingRad) * horizontal,
    target.y + Math.sin(pitch) * distance,
    target.z - Math.cos(state.bearingRad) * horizontal,
  );
  camera.upVector.set(0, 1, 0);
  camera.setTarget(target);
}

function createSystemOrbitMesh(
  scene: Scene,
  layouts: readonly SystemBodyLayout[],
): ReturnType<typeof CreateLineSystem> | null {
  const guides = systemOrbitGuides(layouts);
  if (guides.length === 0) return null;
  const lines = guides.map(({ position, radius }) =>
    Array.from({ length: 97 }, (_, index) => {
      const angle = (index / 96) * Math.PI * 2;
      return new Vector3(
        position.x + Math.cos(angle) * radius,
        position.y,
        position.z + Math.sin(angle) * radius,
      );
    }),
  );
  const orbitMesh = CreateLineSystem(
    'system-semantic-orbits',
    { lines, updatable: false },
    scene,
  );
  orbitMesh.color = new Color3(0.16, 0.34, 0.46);
  orbitMesh.alpha = 0.42;
  orbitMesh.isPickable = false;
  orbitMesh.metadata = {
    spatialLayer: {
      id: 'system-semantic-orbits',
      representation: 'SCHEMATIC',
      meaning: 'known-parent-groups-not-observed-orbital-elements',
    },
    guides,
  };
  return orbitMesh;
}

function selectedSystemBodyId(camera: SystemCameraState): number | null {
  return camera.focus.kind === 'body' ? camera.focus.ref.bodyId : null;
}

function createSystemParentLinkMesh(
  scene: Scene,
  layouts: readonly SystemBodyLayout[],
  selectedBodyId: number | null,
  hoveredBodyId: number | null,
): ReturnType<typeof CreateLineSystem> | null {
  if (selectedBodyId === null && hoveredBodyId === null) return null;
  const byId = new Map(
    layouts.map((layout) => [layout.body.ref.bodyId, layout]),
  );
  const links = layouts.flatMap((layout) => {
    const parent =
      layout.parentBodyId === null ? undefined : byId.get(layout.parentBodyId);
    if (!parent) return [];
    if (
      ![layout.body.ref.bodyId, parent.body.ref.bodyId].some(
        (id) => id === selectedBodyId || id === hoveredBodyId,
      )
    )
      return [];
    return [{ parent, child: layout }];
  });
  if (links.length === 0) return null;
  const mesh = CreateLineSystem(
    'system-parent-links',
    {
      lines: links.map(({ parent, child }) => [
        new Vector3(parent.position.x, parent.position.y, parent.position.z),
        new Vector3(child.position.x, child.position.y, child.position.z),
      ]),
    },
    scene,
  );
  mesh.color = new Color3(0.42, 0.65, 0.74);
  mesh.alpha = 0.72;
  mesh.isPickable = false;
  mesh.metadata = {
    spatialLayer: { id: 'system-parent-links', representation: 'SCHEMATIC' },
    links: links.map(({ parent, child }) => ({
      parentBodyId: parent.body.ref.bodyId,
      bodyId: child.body.ref.bodyId,
    })),
  };
  return mesh;
}

function refreshSystemParentLinks(
  product: BabylonSystemProduct,
  camera: SystemCameraState,
  hoveredBodyId = product.hoveredBodyId,
): BabylonSystemProduct {
  if (
    selectedSystemBodyId(camera) ===
      selectedSystemBodyId(product.cameraState) &&
    hoveredBodyId === product.hoveredBodyId
  )
    return product;
  product.parentLinkMesh?.dispose();
  const parentLinkMesh = createSystemParentLinkMesh(
    product.scene,
    product.bodies.map(({ layout }) => layout),
    selectedSystemBodyId(camera),
    hoveredBodyId,
  );
  return { ...product, parentLinkMesh, hoveredBodyId };
}

function createSystemBodyMesh(
  scene: Scene,
  layout: SystemBodyLayout,
  visual: ReturnType<typeof systemBodyColour>,
): Mesh {
  const { body } = layout;
  const name = `system-body-${body.ref.bodyId}`;
  if (layout.placement === 'unplaced' || body.class?.value === 'Barycentre') {
    const unplaced = layout.placement === 'unplaced';
    const radius = unplaced ? Math.max(0.55, body.displayRadius) : 0.45;
    const lines = unplaced
      ? [
          [
            new Vector3(-radius, 0, 0),
            new Vector3(0, 0, radius),
            new Vector3(radius, 0, 0),
            new Vector3(0, 0, -radius),
            new Vector3(-radius, 0, 0),
          ],
        ]
      : [
          [new Vector3(-radius, 0, 0), new Vector3(radius, 0, 0)],
          [new Vector3(0, 0, -radius), new Vector3(0, 0, radius)],
        ];
    const marker = CreateLineSystem(name, { lines }, scene);
    marker.color = unplaced
      ? new Color3(0.94, 0.67, 0.3)
      : new Color3(0.58, 0.72, 0.78);
    marker.intersectionThreshold = radius * 0.45;
    return marker;
  }
  const mesh = CreateSphere(
    name,
    { diameter: body.displayRadius * 2, segments: 24 },
    scene,
  );
  const material = new StandardMaterial(
    `system-body-material-${body.ref.bodyId}`,
    scene,
  );
  material.diffuseColor = visual.darkCore ? Color3.Black() : visual.diffuse;
  material.emissiveColor = visual.darkCore
    ? new Color3(0.005, 0.005, 0.008)
    : visual.emissive;
  material.specularColor = visual.darkCore
    ? new Color3(0.08, 0.08, 0.1)
    : new Color3(0.24, 0.24, 0.26);
  material.specularPower = 48;
  if (visual.unknown) {
    material.alpha = 0.42;
    material.specularColor = new Color3(0.06, 0.06, 0.07);
  }
  mesh.material = material;
  mesh.onDisposeObservable.addOnce(() => material.dispose());
  return mesh;
}

export const createBabylonSystemScene = (
  engine: AbstractEngine,
  contract: SystemSceneContract,
): BabylonSystemProduct => {
  const scene = new Scene(engine);
  scene.detachControl();
  scene.clearColor = new Color4(0.004, 0.008, 0.016, 1);
  const camera = new FreeCamera('system-camera', Vector3.Zero(), scene);
  scene.activeCamera = camera;
  const light = new HemisphericLight(
    'system-fill',
    new Vector3(-0.3, 0.85, -0.45),
    scene,
  );
  light.intensity = 1.25;

  const layouts = systemBodyLayout(contract.bodies);
  const bodies = layouts.map((layout): BabylonSystemBodyMesh => {
    const body = layout.body;
    const visual = systemBodyColour(body);
    const mesh = createSystemBodyMesh(scene, layout, visual);
    mesh.position.set(layout.position.x, layout.position.y, layout.position.z);
    if (visual.banded && layout.placement === 'placed')
      applyGiantBands(mesh, body.displayRadius);
    mesh.metadata = {
      spatialTarget: {
        kind: 'body',
        ref: body.ref,
      },
      bodyName: body.name,
      markerKind:
        layout.placement === 'unplaced'
          ? 'position-unknown'
          : body.class?.value === 'Barycentre'
            ? 'barycentre'
            : 'body',
      positionUnknownReason: layout.positionUnknownReason,
      bodyFamily: body.bodyFamily,
      classTreatment: visual.unknown ? 'unknown-dim' : 'known',
      representation: {
        position: 'SCHEMATIC',
        bodyFacts: 'AUTHORITATIVE',
      },
    };
    const ringMeshes =
      body.rings?.state === 'PRESENT'
        ? [
            CreateTorus(
              `system-body-ring-${body.ref.bodyId}`,
              {
                diameter: body.displayRadius * 3.2,
                thickness: Math.max(0.06, body.displayRadius * 0.18),
                tessellation: 64,
              },
              scene,
            ),
          ]
        : [];
    for (const ring of ringMeshes) {
      ring.position.copyFrom(mesh.position);
      ring.rotation.x = Math.PI / 2;
      ring.isPickable = false;
      const ringMaterial = new StandardMaterial(
        `system-body-ring-material-${body.ref.bodyId}`,
        scene,
      );
      ringMaterial.disableLighting = true;
      ringMaterial.emissiveColor = new Color3(0.48, 0.56, 0.62);
      ringMaterial.diffuseColor = new Color3(0.2, 0.22, 0.24);
      ringMaterial.alpha = 0.72;
      ring.material = ringMaterial;
      ring.onDisposeObservable.addOnce(() => ringMaterial.dispose());
    }
    return { layout, mesh, ringMeshes };
  });
  const orbitMesh = createSystemOrbitMesh(scene, layouts);
  const parentLinkMesh = createSystemParentLinkMesh(
    scene,
    layouts,
    selectedSystemBodyId(contract.camera),
    null,
  );
  applySystemCamera(camera, contract.camera, bodies);
  const glowMeshes = bodies
    .filter((body) => {
      const identity = `${body.layout.body.class?.value ?? ''} ${body.layout.body.subtype?.value ?? ''}`;
      return (
        body.layout.placement === 'placed' && /star|black hole/i.test(identity)
      );
    })
    .map((body) => body.mesh);
  const visualGlow = createGalaxyVisualFinish(scene, glowMeshes);
  visualGlow.name = 'system-emissive-glow';
  visualGlow.intensity = 0.58;
  const renderedLayers: readonly RenderedLayerReceipt[] = [];
  scene.metadata = {
    spatialScene: {
      kind: 'system',
      revision: contract.revision,
      fidelity: contract.fidelity,
      bodyCount: bodies.length,
      renderedLayers,
      visualProfile: {
        threeDimensional: true,
        bodyFacts: 'authoritative-when-present',
        placement: 'deterministic-schematic',
        physicalScale: false,
      },
    },
  };
  return {
    scene,
    camera,
    cameraState: contract.camera,
    contract,
    bodies,
    orbitMesh,
    parentLinkMesh,
    hoveredBodyId: null,
    visualGlow,
    renderedLayers,
  };
};

function pickSystemTarget(
  engine: AbstractEngine,
  product: BabylonSystemProduct,
  screenX: number,
  screenY: number,
): SpatialTarget | undefined {
  const canvas = engine.getRenderingCanvas();
  const scaleX = canvas?.clientWidth
    ? engine.getRenderWidth() / canvas.clientWidth
    : 1;
  const scaleY = canvas?.clientHeight
    ? engine.getRenderHeight() / canvas.clientHeight
    : 1;
  const bodiesByMesh = new Map<unknown, BabylonSystemBodyMesh>(
    product.bodies
      .filter(
        ({ mesh }) =>
          mesh.isEnabled() &&
          mesh.isVisible &&
          mesh.visibility > 0 &&
          mesh.isPickable,
      )
      .map((body) => [body.mesh, body]),
  );
  const hit = product.scene.pick(screenX * scaleX, screenY * scaleY, (mesh) =>
    bodiesByMesh.has(mesh),
  );
  const body = bodiesByMesh.get(hit?.pickedMesh);
  if (body) return { kind: 'body', ref: body.layout.body.ref };

  // Compact moon glyphs retain a six-CSS-pixel selection radius at any DPR.
  // Native surface hits take priority; the fallback changes no visible size.
  const { camera } = product;
  const transform = camera.getTransformationMatrix();
  const viewport = camera.viewport.toGlobal(
    engine.getRenderWidth(),
    engine.getRenderHeight(),
  );
  const forward = camera.getForwardRay().direction;
  const identity = Matrix.Identity();
  const projected = Vector3.Zero();
  const fromCamera = Vector3.Zero();
  let closest: BabylonSystemBodyMesh | undefined;
  let closestDistanceSq = 6 * 6;
  for (const candidate of bodiesByMesh.values()) {
    const position = candidate.mesh.getAbsolutePosition();
    position.subtractToRef(camera.globalPosition, fromCamera);
    if (Vector3.Dot(fromCamera, forward) <= 0) continue;
    Vector3.ProjectToRef(position, identity, transform, viewport, projected);
    if (
      projected.z < 0 ||
      projected.z > 1 ||
      projected.x < viewport.x ||
      projected.x > viewport.x + viewport.width ||
      projected.y < viewport.y ||
      projected.y > viewport.y + viewport.height
    )
      continue;
    const dx = projected.x / scaleX - screenX;
    const dy = projected.y / scaleY - screenY;
    const distanceSq = dx * dx + dy * dy;
    if (
      distanceSq < closestDistanceSq ||
      (!closest && distanceSq <= closestDistanceSq)
    ) {
      closest = candidate;
      closestDistanceSq = distanceSq;
    }
  }
  return closest ? { kind: 'body', ref: closest.layout.body.ref } : undefined;
}

export const subscribeToBabylonResourceEvents = (
  engine: Pick<
    AbstractEngine,
    'onContextLostObservable' | 'onContextRestoredObservable'
  >,
  backend: SpatialRendererBackend,
  listener: SpatialBackendResourceListener,
): (() => void) => {
  const prefix = backend === 'WEBGPU' ? 'webgpu-device' : 'webgl2-context';
  const lostObserver = engine.onContextLostObservable.add(() => {
    listener({ state: 'lost', detail: `${prefix}-lost` });
  });
  const restoredObserver = engine.onContextRestoredObservable.add(() => {
    listener({ state: 'recovered', detail: `${prefix}-restored` });
  });
  let subscribed = true;

  return () => {
    if (!subscribed) return;
    subscribed = false;
    lostObserver.remove();
    restoredObserver.remove();
  };
};

export const createBabylonSession = (
  engine: AbstractEngine,
  backend: SpatialRendererBackend,
  now: () => number = () => performance.now(),
): SpatialBackendSession => {
  let scene: Scene | null = null;
  let product: BabylonGalaxyProduct | null = null;
  let productContract: GalaxySceneContract | null = null;
  let systemProduct: BabylonSystemProduct | null = null;
  let warmupFramesRemaining = BABYLON_SCENE_WARMUP_FRAMES;
  let readinessAttemptsRemaining = 120;
  let hoveredTargetKey = '';
  // Star marker geometry only depends on zoom (distance). Re-uploading the
  // vertex buffer on every orbit/pan frame churns the GPU and can momentarily
  // drop the instanced field on some drivers; track the last applied scale so
  // pure rotation/pan reuses the existing geometry without a re-upload.
  let lastAppliedStarScale = Number.NaN;
  let transition: {
    from: CameraState;
    to: CameraState;
    startedAt: number;
    durationMs: number;
    target?: SpatialTarget;
    emit: (event: RuntimeEvent) => void;
  } | null = null;

  const applyCameraState = (
    camera: CameraState,
    emit: (event: RuntimeEvent) => void,
  ): void => {
    if (!product) return;
    applyGalaxyCamera(product.camera, camera);
    const scale =
      galaxyStarMarkerSizeLy(camera.distanceLy) / product.markerSizeLy;
    if (scale !== lastAppliedStarScale) {
      lastAppliedStarScale = scale;
      const positions = product.starVertexPositions.map(
        (value) => value * scale,
      );
      for (const batch of [product.catalogueStars, product.finderStars]) {
        batch.mesh.updateVerticesData('position', positions, true);
        batch.mesh.thinInstanceRefreshBoundingInfo(true);
      }
      product.selectedMarker?.scaling.setAll(scale);
      for (const accent of product.stellarAccentMeshes)
        accent.scaling.setAll(scale);
    }
    const referenceGrid = refreshGalaxyReferenceGrid(product, camera);
    updateGalaxyReferenceGridVisibility(referenceGrid, camera);
    updateGalaxyNebulaVisibility(
      product.nebulaMesh,
      product.nebulaCloudMesh,
      product.planetaryNebulaMesh,
      product.nebulaeMetadata,
      camera,
    );
    const previousNebulaReceipt = product.renderedLayers.find(
      (receipt) => receipt.layerId === GALAXY_NEBULAE_LAYER_ID,
    );
    const renderedNebulaCount = product.nebulaeMetadata?.currentlyVisibleCount;
    const nebulaCountChanged =
      previousNebulaReceipt &&
      renderedNebulaCount !== undefined &&
      previousNebulaReceipt.renderedTargetCount !== renderedNebulaCount;
    const renderedLayers = nebulaCountChanged
      ? product.renderedLayers.map((receipt) =>
          receipt === previousNebulaReceipt
            ? {
                ...receipt,
                renderedTargetCount: renderedNebulaCount,
              }
            : receipt,
        )
      : product.renderedLayers;
    product.scene.metadata.spatialScene.renderedLayers = renderedLayers;
    product = {
      ...product,
      cameraState: camera,
      referenceGrid,
      renderedLayers,
    };
    applyDensityCrossfade(product);
    if (productContract) productContract = { ...productContract, camera };
    emit({ type: 'CAMERA_CHANGED', camera });
    if (nebulaCountChanged) {
      emit({
        type: 'CONTRIBUTION_APPLIED',
        contributionId: previousNebulaReceipt.contributionId,
        contributionRevision: previousNebulaReceipt.contributionRevision,
        renderedLayers,
      });
    }
  };

  const cancelTransition = (
    current: typeof transition,
    emit: (event: RuntimeEvent) => void,
  ): void => {
    if (!current) return;
    emit(
      current.target
        ? { type: 'TRANSITION_FINISHED', target: current.target }
        : { type: 'TRANSITION_FINISHED' },
    );
  };

  const moveCamera = (
    camera: CameraState,
    emit: (event: RuntimeEvent) => void,
    durationMs = 0,
    target?: SpatialTarget,
  ): void => {
    if (!product) return;
    transition = null;
    warmupFramesRemaining = BABYLON_SCENE_WARMUP_FRAMES;
    readinessAttemptsRemaining = 120;
    if (durationMs > 0) {
      transition = {
        from: product.cameraState,
        to: camera,
        startedAt: now(),
        durationMs: Math.min(2_000, durationMs),
        target,
        emit,
      };
    } else {
      applyCameraState(camera, emit);
      if (target) emit({ type: 'TRANSITION_FINISHED', target });
    }
  };
  const applySystemCameraState = (
    camera: SystemCameraState,
    emit: (event: RuntimeEvent) => void,
  ): void => {
    if (!systemProduct) return;
    applySystemCamera(systemProduct.camera, camera, systemProduct.bodies);
    systemProduct = refreshSystemParentLinks(systemProduct, camera);
    systemProduct = {
      ...systemProduct,
      cameraState: camera,
      contract: { ...systemProduct.contract, camera },
    };
    emit({ type: 'CAMERA_CHANGED', camera });
  };
  try {
    scene = createDiagnosticScene(engine);
  } catch (error) {
    disposeEngine(engine);
    throw error;
  }

  return {
    backend,
    subscribeResourceEvents: (listener) =>
      subscribeToBabylonResourceEvents(engine, backend, listener),
    resize({ width, height, dpr }: SpatialViewport) {
      engine.setSize(
        Math.max(1, Math.round(width * dpr)),
        Math.max(1, Math.round(height * dpr)),
        true,
      );
      if (product) {
        applyGalaxyCamera(product.camera, product.cameraState);
        const referenceGrid = refreshGalaxyReferenceGrid(
          product,
          product.cameraState,
        );
        updateGalaxyReferenceGridVisibility(referenceGrid, product.cameraState);
        updateGalaxyNebulaVisibility(
          product.nebulaMesh,
          product.nebulaCloudMesh,
          product.planetaryNebulaMesh,
          product.nebulaeMetadata,
          product.cameraState,
        );
        product = { ...product, referenceGrid };
      } else if (systemProduct) {
        applySystemCamera(
          systemProduct.camera,
          systemProduct.cameraState,
          systemProduct.bodies,
        );
      }
    },
    execute(
      command: Exclude<RuntimeCommand, { type: 'RESIZE' }>,
      emit: (event: RuntimeEvent) => void,
    ): RuntimeCommandDispatchResult {
      if (command.type === 'LOAD_SCENE') {
        if (command.scene.kind === 'system') {
          const interruptedTransition = transition;
          transition = null;
          const replacement = createBabylonSystemScene(engine, command.scene);
          const previous = scene;
          scene = replacement.scene;
          systemProduct = replacement;
          product = null;
          productContract = null;
          emit({ type: 'CAMERA_CHANGED', camera: replacement.cameraState });
          hoveredTargetKey = '';
          warmupFramesRemaining = BABYLON_SCENE_WARMUP_FRAMES;
          readinessAttemptsRemaining = 120;
          previous?.dispose();
          emit({
            type: 'SCENE_APPLIED',
            sceneRevision: command.scene.revision,
            renderedLayers: replacement.renderedLayers,
          });
          cancelTransition(interruptedTransition, emit);
          return { status: 'executed' };
        }
        if (
          product &&
          productContract &&
          canPatchGalaxySelection(productContract, command.scene)
        ) {
          const selectedSystemId64 = selectedGalaxySystemId64(command.scene);
          const selectedPoint = product.points.find(
            (point) => point.systemId64 === selectedSystemId64,
          );
          let selectedMarker = product.selectedMarker;
          if (!selectedPoint && selectedMarker) {
            product.visualGlow.removeIncludedOnlyMesh(selectedMarker);
            selectedMarker.dispose(false, true);
            selectedMarker = null;
          } else if (selectedPoint && selectedMarker) {
            selectedMarker.position.set(
              selectedPoint.positionLy.x,
              selectedPoint.positionLy.y,
              selectedPoint.positionLy.z,
            );
          } else if (selectedPoint) {
            selectedMarker = createSelectedSystemMarker(
              product.scene,
              selectedPoint,
              product.markerSizeLy,
            );
            selectedMarker.scaling.setAll(
              galaxyStarMarkerSizeLy(product.cameraState.distanceLy) /
                product.markerSizeLy,
            );
            product.visualGlow.addIncludedOnlyMesh(selectedMarker);
          }
          const systemLayers = galaxySystemsSceneLayers(command.scene);
          const batches = partitionGalaxyStars(
            product.points,
            systemLayers,
            command.scene.selection,
          );
          const catalogueStars = repartitionGalaxyStarBatch(
            product.catalogueStars,
            batches.catalogue,
            product.stellarAccentMeshes,
          );
          const finderStars = repartitionGalaxyStarBatch(
            product.finderStars,
            batches.finder,
            product.stellarAccentMeshes,
          );
          const systemReceipts = galaxySystemsReceipts(
            systemLayers,
            finderStars.points,
          );
          const renderedLayers = product.renderedLayers.map(
            (receipt) =>
              systemReceipts.find(
                (next) =>
                  next.contributionId === receipt.contributionId &&
                  next.layerId === receipt.layerId,
              ) ?? receipt,
          );
          product = {
            ...product,
            catalogueStars,
            finderStars,
            renderedLayers,
            selectedMarker,
            selectedSystemId64,
            selectedRegionId: selectedGalaxyRegionId(command.scene),
          };
          productContract = command.scene;
          applyDensityCrossfade(product);
          updateGalaxyRegionFillAppearance(product, null);
          const spatialScene = product.scene.metadata?.spatialScene as
            | {
                revision?: number;
                renderedLayers?: readonly RenderedLayerReceipt[];
              }
            | undefined;
          if (spatialScene) {
            spatialScene.revision = command.scene.revision;
            spatialScene.renderedLayers = renderedLayers;
          }
          hoveredTargetKey = '';
          warmupFramesRemaining = BABYLON_SCENE_WARMUP_FRAMES;
          readinessAttemptsRemaining = 120;
          emit({
            type: 'SCENE_APPLIED',
            sceneRevision: command.scene.revision,
            renderedLayers: product.renderedLayers,
          });
          return { status: 'executed' };
        }
        const interruptedTransition = transition;
        transition = null;
        const replacement = createBabylonGalaxyScene(engine, command.scene);
        const previous = scene;
        scene = replacement.scene;
        product = replacement;
        productContract = command.scene;
        systemProduct = null;
        lastAppliedStarScale = Number.NaN;
        applyDensityCrossfade(product);
        emit({ type: 'CAMERA_CHANGED', camera: replacement.cameraState });
        hoveredTargetKey = '';
        warmupFramesRemaining = BABYLON_SCENE_WARMUP_FRAMES;
        readinessAttemptsRemaining = 120;
        previous?.dispose();
        emit({
          type: 'SCENE_APPLIED',
          sceneRevision: command.scene.revision,
          renderedLayers: replacement.renderedLayers,
        });
        cancelTransition(interruptedTransition, emit);
        return { status: 'executed' };
      }
      if (command.type === 'PATCH_CONTRIBUTION') {
        if (!product || !productContract) {
          return { status: 'unsupported', command: command.type };
        }
        const currentDensity = catalogueDensitySceneLayer(productContract);
        const currentContribution = productContract.contributions.find(
          (contribution) => contribution.id === command.contribution.id,
        );
        if (
          (currentContribution && currentContribution.owner !== 'CATALOGUE') ||
          !replacesCatalogueDensity(currentDensity, command.contribution)
        ) {
          return { status: 'unsupported', command: command.type };
        }
        if (
          currentContribution &&
          command.contribution.revision <= currentContribution.revision
        ) {
          return { status: 'ignored', reason: 'stale' };
        }

        const nextContract: GalaxySceneContract = {
          ...productContract,
          contributions: currentContribution
            ? productContract.contributions.map((contribution) =>
                contribution.id === command.contribution.id
                  ? command.contribution
                  : contribution,
              )
            : [...productContract.contributions, command.contribution],
        };
        const nextDensity = catalogueDensitySceneLayer(nextContract);
        product.densityMesh?.dispose();
        // Not added to the glow layer: see createBabylonGalaxyScene.
        const densityMesh = createCatalogueDensityMesh(
          product.scene,
          nextDensity,
        );
        const renderedLayers = [
          ...product.renderedLayers.filter(
            (receipt) => receipt.layerId !== 'catalogue-density',
          ),
          ...(nextDensity ? [catalogueDensityReceipt(nextDensity)] : []),
        ];
        const spatialScene = product.scene.metadata?.spatialScene as
          { renderedLayers?: readonly RenderedLayerReceipt[] } | undefined;
        if (spatialScene) spatialScene.renderedLayers = renderedLayers;
        product = { ...product, densityMesh, renderedLayers };
        productContract = nextContract;
        applyDensityCrossfade(product);
        warmupFramesRemaining = BABYLON_SCENE_WARMUP_FRAMES;
        readinessAttemptsRemaining = 120;
        emit({
          type: 'CONTRIBUTION_APPLIED',
          contributionId: command.contribution.id,
          contributionRevision: command.contribution.revision,
          renderedLayers,
        });
        return { status: 'executed' };
      }
      if (command.type === 'SET_CAMERA') {
        if ('focusLy' in command.camera) {
          if (!product) {
            return { status: 'unsupported', command: command.type };
          }
          const camera = normalizeGalaxyCamera(command.camera);
          const durationMs =
            command.transition && !command.transition.reducedMotion
              ? command.transition.durationMs
              : 0;
          if (!Number.isFinite(durationMs) || durationMs < 0) {
            return { status: 'unsupported', command: command.type };
          }
          moveCamera(camera, emit, durationMs);
          return { status: 'executed' };
        }
        if (
          !systemProduct ||
          command.camera.systemId64 !== systemProduct.contract.systemId64
        ) {
          return { status: 'unsupported', command: command.type };
        }
        applySystemCameraState(command.camera, emit);
        return { status: 'executed' };
      }
      if (command.type === 'FLY_TO') {
        if (command.target.kind === 'body' && systemProduct) {
          const focusedBodyId = command.target.ref.bodyId;
          if (
            !systemProduct.bodies.some(
              (body) => body.layout.body.ref.bodyId === focusedBodyId,
            )
          ) {
            return { status: 'unsupported', command: command.type };
          }
          applySystemCameraState(
            {
              ...systemProduct.cameraState,
              focus: command.target,
              semanticDistance: Math.max(
                6,
                Math.min(18, systemProduct.cameraState.semanticDistance),
              ),
              revision: systemProduct.cameraState.revision + 1,
            },
            emit,
          );
          emit({ type: 'TRANSITION_FINISHED', target: command.target });
          return { status: 'executed' };
        }
        if (command.target.kind !== 'system' || !product) {
          return { status: 'unsupported', command: command.type };
        }
        const targetId = command.target.systemId64;
        const point = product.points.find(
          (candidate) => candidate.systemId64 === targetId,
        );
        if (!point) return { status: 'unsupported', command: command.type };
        // End at the local view, or where the camera already is when that is
        // closer. This must not depend on the marker size, which is fixed when
        // the scene is built and says nothing about where the commander is now.
        const distance = Math.max(
          12,
          Math.min(
            product.cameraState.distanceLy,
            GALAXY_LOCAL_VIEW_DISTANCE_LY,
          ),
        );
        moveCamera(
          focusGalaxyCamera(product.cameraState, point.positionLy, distance),
          emit,
          command.reducedMotion ? 0 : 700,
          command.target,
        );
        return { status: 'executed' };
      }
      if (command.type === 'HOVER') {
        if (systemProduct) {
          const target = pickSystemTarget(
            engine,
            systemProduct,
            command.screenX,
            command.screenY,
          );
          const nextKey = targetKey(target);
          if (nextKey !== hoveredTargetKey) {
            hoveredTargetKey = nextKey;
            systemProduct = refreshSystemParentLinks(
              systemProduct,
              systemProduct.cameraState,
              target?.kind === 'body' ? target.ref.bodyId : null,
            );
            warmupFramesRemaining = BABYLON_SCENE_WARMUP_FRAMES;
            emit({
              type: 'TARGET_HOVERED',
              target,
              screenX: command.screenX,
              screenY: command.screenY,
            });
          }
          return { status: 'executed' };
        }
        if (!product) return { status: 'unsupported', command: command.type };
        const target = pickGalaxyTarget(
          engine,
          product,
          command.screenX,
          command.screenY,
          { includeSystemName: true },
        );
        const nextKey = targetKey(target);
        warmupFramesRemaining = BABYLON_SCENE_WARMUP_FRAMES;
        readinessAttemptsRemaining = 120;
        updateGalaxyRegionFillAppearance(
          product,
          target?.kind === 'region' ? Number(target.id) : null,
        );
        if (nextKey !== hoveredTargetKey) {
          hoveredTargetKey = nextKey;
          emit({
            type: 'TARGET_HOVERED',
            target,
            screenX: command.screenX,
            screenY: command.screenY,
          });
        }
        return { status: 'executed' };
      }
      if (command.type === 'CLEAR_HOVER') {
        if (systemProduct) {
          if (hoveredTargetKey) {
            hoveredTargetKey = '';
            systemProduct = refreshSystemParentLinks(
              systemProduct,
              systemProduct.cameraState,
              null,
            );
            warmupFramesRemaining = BABYLON_SCENE_WARMUP_FRAMES;
            emit({ type: 'TARGET_HOVERED' });
          }
          return { status: 'executed' };
        }
        if (!product) return { status: 'unsupported', command: command.type };
        warmupFramesRemaining = BABYLON_SCENE_WARMUP_FRAMES;
        readinessAttemptsRemaining = 120;
        updateGalaxyRegionFillAppearance(product, null);
        if (hoveredTargetKey) {
          hoveredTargetKey = '';
          emit({ type: 'TARGET_HOVERED' });
        }
        return { status: 'executed' };
      }
      if (command.type === 'PICK') {
        if (systemProduct) {
          emit({
            type: 'TARGET_PICKED',
            target: pickSystemTarget(
              engine,
              systemProduct,
              command.screenX,
              command.screenY,
            ),
          });
          return { status: 'executed' };
        }
        if (!product) return { status: 'unsupported', command: command.type };
        const target = pickGalaxyTarget(
          engine,
          product,
          command.screenX,
          command.screenY,
          { allowNearestStar: true },
        );
        emit({
          type: 'TARGET_PICKED',
          target,
        });
        return { status: 'executed' };
      }
      return { status: 'unsupported', command: command.type };
    },
    render() {
      const activeScene = scene;
      if (!activeScene) return false;
      if (transition) {
        const activeTransition = transition;
        const progress = Math.min(
          1,
          Math.max(
            0,
            (now() - activeTransition.startedAt) / activeTransition.durationMs,
          ),
        );
        applyCameraState(
          interpolateGalaxyCamera(
            activeTransition.from,
            activeTransition.to,
            progress,
          ),
          activeTransition.emit,
        );
        if (progress >= 1) {
          transition = null;
          activeTransition.emit(
            activeTransition.target
              ? {
                  type: 'TRANSITION_FINISHED',
                  target: activeTransition.target,
                }
              : { type: 'TRANSITION_FINISHED' },
          );
        }
      }
      // Until every material in a new or patched scene is ready (WebGPU loads
      // shaders asynchronously), present nothing and keep the previous frame
      // on screen. Rendering early drew a frame without whole layers, such as
      // the density glow: the one-frame dark flash. Bounded, so a material that
      // never becomes ready cannot stop the map from drawing.
      if (!activeScene.isReady(true) && readinessAttemptsRemaining > 0) {
        readinessAttemptsRemaining -= 1;
        warmupFramesRemaining = BABYLON_SCENE_WARMUP_FRAMES;
        return true;
      }
      // A demand-rendered WebGPU frame still needs the engine frame boundary:
      // endFrame submits Babylon's queued GPU commands. WebGL can appear to
      // work without it, which previously hid a black WebGPU canvas.
      engine.beginFrame();
      try {
        activeScene.render();
      } finally {
        engine.endFrame();
      }
      warmupFramesRemaining = Math.max(0, warmupFramesRemaining - 1);
      return warmupFramesRemaining > 0 || transition !== null;
    },
    dispose() {
      const currentScene = scene;
      scene = null;
      product = null;
      productContract = null;
      systemProduct = null;
      transition = null;
      try {
        currentScene?.dispose();
      } finally {
        engine.dispose();
      }
    },
  };
};

const createWebGpuSession = async (
  canvas: HTMLCanvasElement,
): Promise<SpatialBackendSession | null> => {
  if (!('gpu' in navigator)) return null;

  const { WebGPUEngine } =
    await import('@babylonjs/core/Engines/webgpuEngine.js');
  if (!(await WebGPUEngine.IsSupportedAsync)) return null;

  const engine = new WebGPUEngine(canvas, {
    antialias: true,
    audioEngine: false,
    canvasTabIndex: -1,
    powerPreference: 'high-performance',
  });
  try {
    await engine.initAsync();
    return createBabylonSession(engine, 'WEBGPU');
  } catch (error) {
    disposeEngine(engine);
    throw error;
  }
};

const createWebGl2Session = (
  canvas: HTMLCanvasElement,
): SpatialBackendSession | null => {
  const options = {
    alpha: false,
    antialias: true,
    depth: true,
    powerPreference: 'high-performance',
    preserveDrawingBuffer: true,
    stencil: true,
  } satisfies WebGLContextAttributes;
  if (!canvas.getContext('webgl2', options)) return null;

  const engine = new Engine(
    canvas,
    true,
    { ...options, audioEngine: false, canvasTabIndex: -1 },
    false,
  );
  if (engine.webGLVersion !== 2) {
    disposeEngine(engine);
    return null;
  }
  return createBabylonSession(engine, 'WEBGL2');
};

export interface BabylonRuntimeDependencies {
  createWebGpu(
    canvas: HTMLCanvasElement,
  ): Promise<SpatialBackendSession | null>;
  createWebGl2(canvas: HTMLCanvasElement): SpatialBackendSession | null;
  replaceCanvas(canvas: HTMLCanvasElement): HTMLCanvasElement;
}

const defaultDependencies: BabylonRuntimeDependencies = {
  createWebGpu: createWebGpuSession,
  createWebGl2: createWebGl2Session,
  replaceCanvas(canvas) {
    const replacement = canvas.cloneNode(false) as HTMLCanvasElement;
    canvas.replaceWith(replacement);
    return replacement;
  },
};

export function createBabylonSpatialRuntime(
  canvas: HTMLCanvasElement,
  onStatus: SpatialRuntimeStatusListener,
  dependencies: BabylonRuntimeDependencies = defaultDependencies,
): SpatialRuntime {
  let activeCanvas = canvas;
  return createManagedSpatialRuntime(
    {
      async createWebGpu() {
        try {
          return await dependencies.createWebGpu(activeCanvas);
        } catch (error) {
          // A failed WebGPU initialization may leave its canvas context-bound.
          // WebGL2 therefore receives a fresh equivalent canvas deterministically.
          activeCanvas = dependencies.replaceCanvas(activeCanvas);
          throw error;
        }
      },
      createWebGl2: () => dependencies.createWebGl2(activeCanvas),
    },
    onStatus,
  );
}
