import type {
  CameraState,
  GalaxySceneContract,
  GalaxySystemPoint,
  SpatialTarget,
  Vec3Ly,
} from './contracts';
import {
  GALAXY_CAMERA_FOV_RAD,
  galaxyCameraPlanePoint,
  galaxyCameraPose,
} from './galaxy-camera';
import { LOCAL_STAR_EXIT_LY } from './galaxy-detail-policy';
import {
  findGalaxyRegionAt,
  GALAXY_REGION_FILL_Y_LY,
  galaxyRegionsSceneLayer,
  type GalaxyRegionMapSource,
} from './galaxy-regions';

export type GalaxyLabelKind = 'region' | 'system';

export type GalaxyLabelCandidate = Readonly<{
  key: string;
  kind: GalaxyLabelKind;
  text: string;
  positionLy: Vec3Ly;
  target: SpatialTarget;
  selected: boolean;
  hovered: boolean;
  current: boolean;
  reference?: boolean;
  source?: 'finder' | 'catalogue' | 'anchor';
  point?: GalaxySystemPoint;
  screenDistancePx?: number;
}>;

export type ProjectedGalaxyLabel = GalaxyLabelCandidate &
  Readonly<{
    xPx: number;
    yPx: number;
    anchorXPx: number;
    anchorYPx: number;
    leaderEndXPx: number;
    placement: 'map' | 'atlas-left' | 'atlas-right';
    priority: number;
    /**
     * A region name that would overlap a more important name is drawn faded
     * instead of being moved. Pass the muted keys back as `previouslyMuted`
     * on the next layout so a name does not flicker at the edge of a clash.
     */
    muted: boolean;
  }>;

export type GalaxyLabelViewport = Readonly<{
  width: number;
  height: number;
}>;

export type GalaxyLabelLayoutOptions = Readonly<{
  safeArea?: Partial<{
    top: number;
    right: number;
    bottom: number;
    left: number;
  }>;
  maximumSystemLabels?: number;
  /**
   * Region names muted in the previous layout. A muted name only returns to
   * full strength once it has clear room, so a zoom cannot flick it on and off.
   */
  previouslyMuted?: ReadonlySet<string>;
  /**
   * The region lookup grid. With it, a region whose name point has left the
   * screen keeps its name at the nearest visible point inside the region, so
   * zooming into a region never loses its name. Without it such names are
   * omitted, as before.
   */
  regionLookup?: GalaxyRegionMapSource;
}>;

type ScreenProjection = Readonly<{
  xPx: number;
  yPx: number;
  depthLy: number;
}>;

type LabelBox = Readonly<{
  left: number;
  right: number;
  top: number;
  bottom: number;
}>;

const SYSTEM_LABEL_DISTANCE_LY = 30_000;
const DEFAULT_MAXIMUM_SYSTEM_LABELS = 24;
export const MAXIMUM_SYSTEM_LABEL_CANDIDATES = 48;
const MAXIMUM_FINDER_LABEL_CANDIDATES = 24;

export type GalaxyLabelCandidateOptions = Readonly<{
  hoveredSystemId64?: string;
  anchor?: GalaxySystemPoint;
  viewport?: GalaxyLabelViewport;
}>;

/** Name budgets only: the shared streaming/detail thresholds remain unchanged. */
export function nearbyCatalogueLabelLimit(distanceLy: number): number {
  if (distanceLy > LOCAL_STAR_EXIT_LY) return 0;
  if (distanceLy <= 500) return 12;
  if (distanceLy <= 1_500) return 8;
  if (distanceLy <= 3_500) return 4;
  return 2;
}

function subtract(left: Vec3Ly, right: Vec3Ly): Vec3Ly {
  return {
    x: left.x - right.x,
    y: left.y - right.y,
    z: left.z - right.z,
  };
}

function dot(left: Vec3Ly, right: Vec3Ly): number {
  return left.x * right.x + left.y * right.y + left.z * right.z;
}

function cross(left: Vec3Ly, right: Vec3Ly): Vec3Ly {
  return {
    x: left.y * right.z - left.z * right.y,
    y: left.z * right.x - left.x * right.z,
    z: left.x * right.y - left.y * right.x,
  };
}

function length(value: Vec3Ly): number {
  return Math.hypot(value.x, value.y, value.z);
}

function normalize(value: Vec3Ly): Vec3Ly {
  const magnitude = length(value);
  if (magnitude === 0) throw new RangeError('Cannot normalize a zero vector');
  return {
    x: value.x / magnitude,
    y: value.y / magnitude,
    z: value.z / magnitude,
  };
}

function targetKey(target: SpatialTarget): string {
  if (target.kind === 'system') return `system:${target.systemId64}`;
  if ('id' in target) return `${target.kind}:${target.id}`;
  return target.kind;
}

type SystemSourceLayer = Readonly<{
  source: 'finder' | 'catalogue';
  payload: unknown;
}>;

type SystemSources = Readonly<{
  layers: readonly SystemSourceLayer[];
  index: ReadonlyMap<string, GalaxySystemPoint>;
  finder: ReadonlySet<string>;
  catalogue: readonly GalaxySystemPoint[];
}>;

const pointPayloads = new WeakMap<object, readonly GalaxySystemPoint[]>();
const contributionSources = new WeakMap<
  GalaxySceneContract['contributions'],
  SystemSources
>();
let recentSources: SystemSources | undefined;

function acceptedPoints(payload: unknown): readonly GalaxySystemPoint[] {
  if (!payload || typeof payload !== 'object') return [];
  const cached = pointPayloads.get(payload);
  if (cached) return cached;
  const systems = (payload as { systems?: unknown }).systems;
  if (!Array.isArray(systems)) return [];
  const points = systems.filter((value): value is GalaxySystemPoint => {
    if (!value || typeof value !== 'object') return false;
    const point = value as GalaxySystemPoint;
    return (
      typeof point.systemId64 === 'string' &&
      typeof point.name === 'string' &&
      !!point.positionLy &&
      [point.positionLy.x, point.positionLy.y, point.positionLy.z].every(
        Number.isFinite,
      )
    );
  });
  pointPayloads.set(payload, points);
  return points;
}

function systemSources(scene: GalaxySceneContract): SystemSources {
  const cached = contributionSources.get(scene.contributions);
  if (cached) return cached;
  const layers: SystemSourceLayer[] = [];
  for (const contribution of scene.contributions) {
    for (const layer of contribution.layers) {
      if (contribution.owner === 'FINDER' && layer.id === 'finder-systems') {
        layers.push({ source: 'finder', payload: layer.payload });
      } else if (
        contribution.owner === 'CATALOGUE' &&
        (layer.id === 'catalogue-systems' || layer.id === 'catalogue-stars')
      ) {
        layers.push({ source: 'catalogue', payload: layer.payload });
      }
    }
  }
  // Scene selection/camera updates can rebuild the contribution array while
  // retaining immutable payloads. They must not rebuild the catalogue index.
  if (
    recentSources &&
    recentSources.layers.length === layers.length &&
    layers.every(
      (layer, index) =>
        layer.source === recentSources?.layers[index].source &&
        layer.payload === recentSources?.layers[index].payload,
    )
  ) {
    contributionSources.set(scene.contributions, recentSources);
    return recentSources;
  }
  const index = new Map<string, GalaxySystemPoint>();
  const catalogue = new Map<string, GalaxySystemPoint>();
  const finder = new Set<string>();
  for (const layer of layers.filter((layer) => layer.source === 'catalogue')) {
    for (const point of acceptedPoints(layer.payload)) {
      catalogue.set(point.systemId64, point);
      index.set(point.systemId64, point);
    }
  }
  for (const layer of layers.filter((layer) => layer.source === 'finder')) {
    for (const point of acceptedPoints(layer.payload)) {
      const cataloguePoint = index.get(point.systemId64);
      index.set(
        point.systemId64,
        cataloguePoint?.primaryStar
          ? {
              ...point,
              // The viewport catalogue carries the raw class (e.g. G2).
              // Finder can carry only a broader type (e.g. G); its richer
              // summary must not replace that catalogue evidence.
              primaryStar: {
                ...point.primaryStar,
                ...cataloguePoint.primaryStar,
              },
            }
          : point,
      );
      finder.add(point.systemId64);
    }
  }
  const sources = { layers, index, catalogue: [...catalogue.values()], finder };
  contributionSources.set(scene.contributions, sources);
  recentSources = sources;
  return sources;
}

/** Stable id64 lookup, indexed only when accepted source payloads change. */
export function galaxySystemPointIndex(
  scene: GalaxySceneContract,
): ReadonlyMap<string, GalaxySystemPoint> {
  return systemSources(scene).index;
}

type NearbyPoint = Readonly<{
  point: GalaxySystemPoint;
  screenDistancePx: number;
  depthLy: number;
}>;

type NearbyCache = Readonly<{
  cameraKey: string;
  points: readonly NearbyPoint[];
}>;
const nearbyCache = new WeakMap<SystemSources, NearbyCache>();

function nearbyPoints(
  sources: SystemSources,
  camera: CameraState,
  viewport: GalaxyLabelViewport,
): readonly NearbyPoint[] {
  const limit = nearbyCatalogueLabelLimit(camera.distanceLy);
  if (!limit) return [];
  const cameraKey = [
    camera.focusLy.x,
    camera.focusLy.y,
    camera.focusLy.z,
    camera.distanceLy,
    camera.bearingRad,
    camera.pitchRad,
    camera.projection,
    viewport.width,
    viewport.height,
  ].join(':');
  const cached = nearbyCache.get(sources);
  if (cached?.cameraKey === cameraKey) return cached.points;
  const points: NearbyPoint[] = [];
  const project = galaxyLabelProjector(camera, viewport);
  // One retrieval pass on settled view/source changes. Keep only the bounded
  // nearest set; never sort the streamed catalogue or pass it to the solver.
  for (const point of sources.catalogue) {
    const screen = project(point.positionLy);
    if (
      !screen ||
      screen.xPx < 0 ||
      screen.xPx > viewport.width ||
      screen.yPx < 0 ||
      screen.yPx > viewport.height
    )
      continue;
    const entry = {
      point,
      screenDistancePx: Math.hypot(
        screen.xPx - viewport.width / 2,
        screen.yPx - viewport.height / 2,
      ),
      depthLy: screen.depthLy,
    };
    const insertion = points.findIndex(
      (other) => compareNearby(entry, other) < 0,
    );
    if (insertion >= 0) points.splice(insertion, 0, entry);
    else if (points.length < limit) points.push(entry);
    if (points.length > limit) points.pop();
  }
  nearbyCache.set(sources, { cameraKey, points });
  return points;
}

function compareNearby(left: NearbyPoint, right: NearbyPoint): number {
  return (
    left.screenDistancePx - right.screenDistancePx ||
    left.depthLy - right.depthLy ||
    left.point.systemId64.localeCompare(right.point.systemId64)
  );
}

/**
 * Collect a bounded set of real named targets at a settled camera. Catalogue
 * retrieval is cached independently of selection/hover, so pointer movement
 * only ranks this small candidate set. Region anchors remain source-derived.
 */
export function galaxyLabelCandidates(
  scene: GalaxySceneContract,
  hoveredRegionId?: string,
  options: GalaxyLabelCandidateOptions = {},
): GalaxyLabelCandidate[] {
  const selectedKeys = new Set(scene.selection.map(targetKey));
  const sources = systemSources(scene);
  const viewport = options.viewport ?? { width: 1_000, height: 500 };
  const systems = new Map<string, GalaxyLabelCandidate>();
  function add(point: GalaxySystemPoint, screenDistancePx?: number): void {
    const target = { kind: 'system', systemId64: point.systemId64 } as const;
    const key = targetKey(target);
    if (systems.has(key)) return;
    const reference = options.anchor?.systemId64 === point.systemId64;
    systems.set(key, {
      key,
      kind: 'system',
      text: point.name,
      positionLy: point.positionLy,
      target,
      selected: selectedKeys.has(key),
      hovered: options.hoveredSystemId64 === point.systemId64,
      current: false,
      reference,
      source: sources.finder.has(point.systemId64)
        ? 'finder'
        : reference
          ? 'anchor'
          : 'catalogue',
      point,
      screenDistancePx,
    });
  }
  for (const target of scene.selection) {
    if (target.kind !== 'system') continue;
    const point = sources.index.get(target.systemId64);
    if (point) add(point);
    if (systems.size >= MAXIMUM_SYSTEM_LABEL_CANDIDATES) break;
  }
  const hovered = options.hoveredSystemId64
    ? sources.index.get(options.hoveredSystemId64)
    : undefined;
  if (hovered) add(hovered);
  if (options.anchor)
    add(sources.index.get(options.anchor.systemId64) ?? options.anchor);
  let finderCount = 0;
  for (const id64 of sources.finder) {
    const point = sources.index.get(id64);
    if (point) add(point);
    if (++finderCount >= MAXIMUM_FINDER_LABEL_CANDIDATES) break;
  }
  for (const nearby of nearbyPoints(sources, scene.camera, viewport)) {
    add(
      sources.index.get(nearby.point.systemId64) ?? nearby.point,
      nearby.screenDistancePx,
    );
  }
  const candidates = [...systems.values()]
    .sort(
      (left, right) =>
        priority(right) - priority(left) ||
        (left.screenDistancePx ?? Infinity) -
          (right.screenDistancePx ?? Infinity) ||
        length(subtract(left.positionLy, scene.camera.focusLy)) -
          length(subtract(right.positionLy, scene.camera.focusLy)) ||
        left.key.localeCompare(right.key),
    )
    .slice(0, MAXIMUM_SYSTEM_LABEL_CANDIDATES);

  const regions = galaxyRegionsSceneLayer(scene);
  if (!regions) return candidates;
  const currentRegion = findGalaxyRegionAt(
    regions.payload.lookup,
    scene.camera.focusLy,
  );
  for (const region of regions.payload.regions) {
    const target = { kind: 'region', id: String(region.id) } as const;
    candidates.push({
      key: targetKey(target),
      kind: 'region',
      text: region.name,
      positionLy: region.labelLy,
      target,
      selected: selectedKeys.has(targetKey(target)),
      hovered: hoveredRegionId === target.id,
      current: currentRegion?.id === region.id,
    });
  }
  return candidates;
}

/** Match Babylon's perspective/orthographic camera basis in CSS pixels. */
export function projectGalaxyLabel(
  pointLy: Vec3Ly,
  camera: CameraState,
  viewport: GalaxyLabelViewport,
): ScreenProjection | null {
  return galaxyLabelProjector(camera, viewport)(pointLy);
}

function galaxyLabelProjector(
  camera: CameraState,
  viewport: GalaxyLabelViewport,
): (pointLy: Vec3Ly) => ScreenProjection | null {
  if (
    !Number.isFinite(viewport.width) ||
    viewport.width <= 0 ||
    !Number.isFinite(viewport.height) ||
    viewport.height <= 0
  ) {
    throw new RangeError('Label viewport must have finite positive dimensions');
  }
  const pose = galaxyCameraPose(camera);
  const forward = normalize(subtract(pose.targetLy, pose.positionLy));
  const right = normalize(cross(pose.up, forward));
  const minimumDepth = Math.max(0.01, camera.distanceLy / 100_000);
  const tangent = Math.tan(GALAXY_CAMERA_FOV_RAD / 2);
  const aspectRatio = viewport.width / viewport.height;
  return (pointLy) => {
    const delta = subtract(pointLy, pose.positionLy);
    const depthLy = dot(delta, forward);
    if (depthLy <= minimumDepth) return null;
    const cameraX = dot(delta, right);
    const cameraY = dot(delta, pose.up);
    const halfHeight =
      camera.projection === 'perspective'
        ? depthLy * tangent
        : camera.distanceLy * tangent;
    const halfWidth = halfHeight * aspectRatio;
    if (halfWidth <= 0 || halfHeight <= 0) return null;
    return {
      xPx: (cameraX / halfWidth / 2 + 0.5) * viewport.width,
      yPx: (0.5 - cameraY / halfHeight / 2) * viewport.height,
      depthLy,
    };
  };
}

function priority(candidate: GalaxyLabelCandidate): number {
  if (candidate.kind === 'system') {
    if (candidate.selected) return 1_000;
    if (candidate.hovered) return 990;
    if (candidate.reference) return 980;
    if (candidate.source === 'finder') return 970;
    return 700;
  }
  if (candidate.kind === 'region' && candidate.selected) return 950;
  // Region hover is pure emphasis (surfaced via `hovered` on the label and
  // styled in CSS) and must NOT change the solver's sort order — otherwise the
  // hovered candidate jumps to the front of the greedy pass and reflows every
  // neighbouring label as the pointer moves. See the hover-stability test.
  if (candidate.current) return 850;
  if (candidate.text === 'Galactic Centre') return 650;
  return 100;
}

function estimateBox(
  candidate: GalaxyLabelCandidate,
  xPx: number,
  yPx: number,
): LabelBox {
  const width = Math.min(
    candidate.kind === 'system' ? 230 : 180,
    Math.max(
      66,
      candidate.text.length * (candidate.kind === 'system' ? 7 : 6.4) + 22,
    ),
  );
  const height =
    candidate.kind === 'system' ? 28 : candidate.text.length > 22 ? 34 : 22;
  return {
    left: xPx - width / 2,
    right: xPx + width / 2,
    top: yPx - height / 2,
    bottom: yPx + height / 2,
  };
}

/**
 * The box a region name's text actually covers (0.63rem uppercase, about
 * 7.2 px a character on one line). Fading only on a real overlap keeps names
 * that merely sit near each other at full strength.
 */
function regionTextBox(
  candidate: GalaxyLabelCandidate,
  xPx: number,
  yPx: number,
): LabelBox {
  const width = Math.min(224, candidate.text.length * 7.2 + 4);
  const height = 14;
  return {
    left: xPx - width / 2,
    right: xPx + width / 2,
    top: yPx - height / 2,
    bottom: yPx + height / 2,
  };
}

const LABEL_GAP_PX = 6;
/** Extra room a faded region name needs before it returns to full strength. */
const UNMUTE_CLEARANCE_PX = 8;
/** Steps tried between the screen edge and the centre to stay inside a region. */
const REGION_INSIDE_SEARCH_STEPS = 24;

function overlaps(
  left: LabelBox,
  right: LabelBox,
  gap: number = LABEL_GAP_PX,
): boolean {
  return !(
    left.right + gap <= right.left ||
    left.left >= right.right + gap ||
    left.bottom + gap <= right.top ||
    left.top >= right.bottom + gap
  );
}

/**
 * Deterministic label solver. Region names never move away from their own
 * region: each sits on its region's name point, or, once that point has left
 * the screen, at the nearest visible point still inside the region. Where two
 * names would overlap, the less important one is drawn faded rather than
 * pushed aside, so a zoom never sends names jumping about. Every region whose
 * name point is in frame is still named: priority only controls emphasis.
 * System labels remain deliberately budgeted and collision-managed.
 */
export function layoutGalaxyLabels(
  candidates: readonly GalaxyLabelCandidate[],
  camera: CameraState,
  viewport: GalaxyLabelViewport,
  options: GalaxyLabelLayoutOptions = {},
): ProjectedGalaxyLabel[] {
  const horizontal = Math.min(34, Math.max(14, viewport.width * 0.025));
  const vertical = Math.min(28, Math.max(12, viewport.height * 0.035));
  const safe = {
    top: Math.max(vertical, options.safeArea?.top ?? 0),
    right: Math.max(horizontal, options.safeArea?.right ?? 0),
    bottom: Math.max(vertical, options.safeArea?.bottom ?? 0),
    left: Math.max(horizontal, options.safeArea?.left ?? 0),
  };
  const centre = { x: viewport.width / 2, y: viewport.height / 2 };
  const project = galaxyLabelProjector(camera, viewport);
  const inFrame = (screen: ScreenProjection): boolean =>
    screen.xPx >= 0 &&
    screen.xPx <= viewport.width &&
    screen.yPx >= 0 &&
    screen.yPx <= viewport.height;
  const projected = candidates.flatMap((candidate) => {
    const screen = project(candidate.positionLy);
    if (!screen) return [];
    if (
      candidate.kind === 'system' &&
      camera.distanceLy > SYSTEM_LABEL_DISTANCE_LY &&
      !candidate.selected &&
      !candidate.hovered &&
      !candidate.reference
    ) {
      return [];
    }
    return [{ candidate, screen, priority: priority(candidate) }];
  });
  projected.sort((left, right) => {
    if (left.priority !== right.priority) return right.priority - left.priority;
    if (left.candidate.kind === 'region' && right.candidate.kind === 'region') {
      // A name whose own point is on screen claims its spot before a name
      // that has slid in from off screen; otherwise the order is fixed, so
      // which of two clashing names fades never depends on the camera.
      const leftInFrame = inFrame(left.screen);
      if (leftInFrame !== inFrame(right.screen)) return leftInFrame ? -1 : 1;
      return left.candidate.key.localeCompare(right.candidate.key);
    }
    const leftDistance = Math.hypot(
      left.screen.xPx - centre.x,
      left.screen.yPx - centre.y,
    );
    const rightDistance = Math.hypot(
      right.screen.xPx - centre.x,
      right.screen.yPx - centre.y,
    );
    return (
      leftDistance - rightDistance ||
      left.screen.depthLy - right.screen.depthLy ||
      left.candidate.key.localeCompare(right.candidate.key)
    );
  });

  const accepted: ProjectedGalaxyLabel[] = [];
  const occupiedBoxes: LabelBox[] = [];
  let systemCount = 0;
  const maximumSystems =
    options.maximumSystemLabels ?? DEFAULT_MAXIMUM_SYSTEM_LABELS;
  const clampIntoSafe = (
    candidate: GalaxyLabelCandidate,
    xPx: number,
    yPx: number,
  ): { x: number; y: number } => {
    const box = estimateBox(candidate, xPx, yPx);
    const halfWidth = (box.right - box.left) / 2;
    const halfHeight = (box.bottom - box.top) / 2;
    return {
      x: clamp(
        xPx,
        safe.left + halfWidth,
        viewport.width - safe.right - halfWidth,
      ),
      y: clamp(
        yPx,
        safe.top + halfHeight,
        viewport.height - safe.bottom - halfHeight,
      ),
    };
  };
  const fits = (box: LabelBox, gap: number): boolean =>
    box.left >= safe.left &&
    box.right <= viewport.width - safe.right &&
    box.top >= safe.top &&
    box.bottom <= viewport.height - safe.bottom &&
    !occupiedBoxes.some((occupied) => overlaps(occupied, box, gap));
  const clear = (box: LabelBox, gap: number): boolean =>
    !occupiedBoxes.some((occupied) => overlaps(occupied, box, gap));
  /**
   * The visible point nearest a region's off-screen name point that is still
   * inside that region: start at the screen edge towards the name point and
   * walk towards the centre until the region lookup agrees.
   */
  const insideRegionOnScreen = (
    candidate: GalaxyLabelCandidate,
    screen: ScreenProjection,
  ): { x: number; y: number } | null => {
    const lookup = options.regionLookup;
    if (!lookup || candidate.target.kind !== 'region') return null;
    const regionId = candidate.target.id;
    const edge = clampIntoSafe(candidate, screen.xPx, screen.yPx);
    for (let step = 0; step <= REGION_INSIDE_SEARCH_STEPS; step += 1) {
      const t = step / REGION_INSIDE_SEARCH_STEPS;
      const point = {
        x: edge.x + (centre.x - edge.x) * t,
        y: edge.y + (centre.y - edge.y) * t,
      };
      // The region drawn under this pixel lies on the fill plane, not on the
      // (possibly elevated) focus plane.
      const world = galaxyCameraPlanePoint(
        camera,
        point,
        viewport,
        GALAXY_REGION_FILL_Y_LY,
      );
      if (!world) continue;
      const region = findGalaxyRegionAt(lookup, world);
      if (region && String(region.id) === regionId) return point;
    }
    return null;
  };

  for (const entry of projected) {
    if (entry.candidate.kind === 'system') {
      if (systemCount >= maximumSystems) continue;
      // A system chip is an interactive button: keep it beside its star, not
      // over it, so the star itself stays clickable for a spatial pick.
      const xPx = entry.screen.xPx + systemLabelOffsetX(entry.candidate);
      const yPx = entry.screen.yPx;
      const box = estimateBox(entry.candidate, xPx, yPx);
      if (!fits(box, LABEL_GAP_PX)) continue;
      accepted.push({
        ...entry.candidate,
        xPx,
        yPx,
        anchorXPx: entry.screen.xPx,
        anchorYPx: entry.screen.yPx,
        leaderEndXPx: xPx,
        placement: 'map',
        priority: entry.priority,
        muted: false,
      });
      occupiedBoxes.push(box);
      systemCount += 1;
      continue;
    }
    const position = inFrame(entry.screen)
      ? clampIntoSafe(entry.candidate, entry.screen.xPx, entry.screen.yPx)
      : insideRegionOnScreen(entry.candidate, entry.screen);
    if (!position) continue;
    const box = regionTextBox(entry.candidate, position.x, position.y);
    const emphasised =
      entry.candidate.selected ||
      entry.candidate.hovered ||
      entry.candidate.current;
    // A name that was faded needs clear room before it returns; one that was
    // shown fades only once it actually touches a more important name.
    const muted =
      !emphasised &&
      !clear(
        box,
        options.previouslyMuted?.has(entry.candidate.key)
          ? LABEL_GAP_PX + UNMUTE_CLEARANCE_PX
          : 0,
      );
    accepted.push({
      ...entry.candidate,
      xPx: position.x,
      yPx: position.y,
      anchorXPx: entry.screen.xPx,
      anchorYPx: entry.screen.yPx,
      leaderEndXPx: position.x,
      placement: 'map',
      priority: entry.priority,
      muted,
    });
    // A faded name is background: it never makes another name fade.
    if (!muted) occupiedBoxes.push(box);
  }
  return accepted;
}

/** Gap between a star and the left edge of its label chip, in CSS pixels. */
export const SYSTEM_LABEL_GAP_PX = 12;

function systemLabelOffsetX(candidate: GalaxyLabelCandidate): number {
  const box = estimateBox(candidate, 0, 0);
  return (box.right - box.left) / 2 + SYSTEM_LABEL_GAP_PX;
}

function clamp(value: number, minimum: number, maximum: number): number {
  if (maximum < minimum) return (minimum + maximum) / 2;
  return Math.min(maximum, Math.max(minimum, value));
}
