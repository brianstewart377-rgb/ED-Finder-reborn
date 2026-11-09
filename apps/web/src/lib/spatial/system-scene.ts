import type { SystemDetail, SystemDetailBody } from '$lib/api/client';
import { stellarFamilyFromCatalogue } from './stellar-presentation';

import type {
  BodyFamily,
  BodyVisualDescriptor,
  SpatialTarget,
  SystemCameraState,
  SystemSceneContract,
  Truth,
} from './contracts';

const factual = <T>(value: T, source?: string): Truth<T> => ({
  value,
  representation: 'AUTHORITATIVE',
  ...(source ? { provenance: [{ source }] } : {}),
});

const derived = <T>(value: T, source: string, note: string): Truth<T> => ({
  value,
  representation: 'DERIVED',
  provenance: [{ source, note }],
});

const BODY_TYPE_LABELS: Readonly<Record<string, string>> = {
  star: 'Star',
  planet: 'Planet',
  barycentre: 'Barycentre',
  belt_cluster: 'Belt cluster',
};

/** Display label for a V3 public body-type code. */
export function bodyTypeLabel(code: string | null | undefined): string | null {
  if (!code) return null;
  return BODY_TYPE_LABELS[code] ?? code.replaceAll('_', ' ');
}

/** Classify the retained class fact for presentation, never from other body data. */
export function bodyFamilyFromClass(bodyClass: string | null): BodyFamily {
  switch (bodyClass?.trim().toLowerCase()) {
    case 'earth-like world':
      return { family: 'earth-like' };
    case 'water world':
      return { family: 'water-world' };
    case 'ammonia world':
      return { family: 'ammonia-world' };
    case 'high metal content world':
      return { family: 'high-metal-content' };
    case 'metal-rich body':
      return { family: 'metal-rich' };
    case 'rocky body':
      return { family: 'rocky' };
    case 'rocky ice world':
    case 'rocky ice body':
      return { family: 'rocky-ice' };
    case 'icy body':
      return { family: 'icy' };
    case 'class i gas giant':
      return { family: 'gas-giant', variant: 'I' };
    case 'class ii gas giant':
      return { family: 'gas-giant', variant: 'II' };
    case 'class iii gas giant':
      return { family: 'gas-giant', variant: 'III' };
    case 'class iv gas giant':
      return { family: 'gas-giant', variant: 'IV' };
    case 'class v gas giant':
      return { family: 'gas-giant', variant: 'V' };
    case 'gas giant with water-based life':
    case 'gas giant with water based life':
      return { family: 'gas-giant-life', variant: 'water' };
    case 'gas giant with ammonia-based life':
    case 'gas giant with ammonia based life':
      return { family: 'gas-giant-life', variant: 'ammonia' };
    case 'helium-rich gas giant':
    case 'helium gas giant':
      return { family: 'helium-gas-giant' };
    case 'water giant':
      return { family: 'water-giant' };
    default:
      return { family: 'unknown' };
  }
}

type BodyFamilyPresentation = Readonly<{
  label: string;
  colour: readonly [number, number, number];
}>;

/** The legend and renderer share a restrained, illustrative (not observed) palette. */
export const BODY_FAMILY_PRESENTATIONS = {
  'earth-like': { label: 'Earth-like', colour: [0.16, 0.58, 0.57] },
  'water-world': { label: 'Water world', colour: [0.12, 0.32, 0.64] },
  'ammonia-world': { label: 'Ammonia world', colour: [0.62, 0.39, 0.22] },
  'high-metal-content': {
    label: 'High metal content',
    colour: [0.52, 0.46, 0.4],
  },
  'metal-rich': { label: 'Metal-rich', colour: [0.34, 0.3, 0.28] },
  rocky: { label: 'Rocky', colour: [0.72, 0.57, 0.4] },
  'rocky-ice': { label: 'Rocky ice', colour: [0.62, 0.7, 0.76] },
  icy: { label: 'Icy', colour: [0.82, 0.92, 0.97] },
  'gas-giant-I': { label: 'Gas giant · I', colour: [0.8, 0.78, 0.67] },
  'gas-giant-II': { label: 'Gas giant · II', colour: [0.88, 0.76, 0.53] },
  'gas-giant-III': { label: 'Gas giant · III', colour: [0.42, 0.56, 0.68] },
  'gas-giant-IV': { label: 'Gas giant · IV', colour: [0.67, 0.35, 0.24] },
  'gas-giant-V': { label: 'Gas giant · V', colour: [0.46, 0.15, 0.13] },
  'gas-giant-life-water': {
    label: 'Life-bearing giant · water',
    colour: [0.41, 0.63, 0.46],
  },
  'gas-giant-life-ammonia': {
    label: 'Life-bearing giant · ammonia',
    colour: [0.61, 0.45, 0.68],
  },
  'helium-gas-giant': { label: 'Helium gas giant', colour: [0.88, 0.84, 0.75] },
  'water-giant': { label: 'Water giant', colour: [0.64, 0.83, 0.94] },
  unknown: { label: 'Unknown class', colour: [0.48, 0.48, 0.52] },
} as const satisfies Readonly<Record<string, BodyFamilyPresentation>>;

export function bodyFamilyPresentation(
  bodyFamily: BodyFamily,
): BodyFamilyPresentation {
  if (bodyFamily.family === 'gas-giant') {
    return BODY_FAMILY_PRESENTATIONS[`gas-giant-${bodyFamily.variant}`];
  }
  if (bodyFamily.family === 'gas-giant-life') {
    return BODY_FAMILY_PRESENTATIONS[`gas-giant-life-${bodyFamily.variant}`];
  }
  return BODY_FAMILY_PRESENTATIONS[bodyFamily.family];
}

/** Spectral + luminosity class as catalogued, e.g. `G2 V`. */
export function stellarClassification(
  body: Pick<SystemDetailBody, 'spectralClass' | 'luminosityClass'>,
): string | null {
  const spectral = body.spectralClass?.trim();
  if (!spectral) return null;
  const luminosity = body.luminosityClass?.trim();
  return luminosity ? `${spectral} ${luminosity}` : spectral;
}

/**
 * Presentation-only stellar subtype phrased the way the shared stellar
 * presentation recognises classes (e.g. `G (G2 V) Star`, `White Dwarf (DA)
 * Star`). Derived from the catalogued spectral/luminosity class; never shown
 * as a catalogue fact.
 */
export function stellarPresentationSubtype(
  body: Pick<
    SystemDetailBody,
    'bodyType' | 'spectralClass' | 'luminosityClass'
  >,
): string | null {
  if (body.bodyType !== 'star') return null;
  const spectral = body.spectralClass?.trim();
  const full = stellarClassification(body);
  if (!spectral || !full) return null;
  const family = stellarFamilyFromCatalogue(spectral);
  if (family === 'neutron') return 'Neutron Star';
  if (family === 'supermassive-black-hole') return 'Supermassive Black Hole';
  if (family === 'black-hole') return 'Black Hole';
  if (family === 'white-dwarf') return `White Dwarf (${full}) Star`;
  if (family === 'wolf-rayet') return `Wolf-Rayet (${full}) Star`;
  if (family === 'TTS') return `T Tauri (${full}) Star`;
  if (family === 'AeBe') return `Herbig Ae/Be (${full}) Star`;
  if (family === 'carbon') return `${full} (Carbon) Star`;
  if (family.length === 1) return `${family} (${full}) Star`;
  return `${full} Star`;
}

function bodyDisplayRadius(body: SystemDetailBody): number {
  const identity = `${bodyTypeLabel(body.bodyType) ?? ''} ${
    stellarPresentationSubtype(body) ?? ''
  }`.toLowerCase();
  if (identity.includes('black hole')) return 1.6;
  if (identity.includes('star')) return identity.includes('dwarf') ? 1.45 : 2.2;
  if (identity.includes('belt')) return 0.28;
  return 0.68;
}

function ringDescriptor(
  body: SystemDetailBody,
  source: string,
): BodyVisualDescriptor['rings'] {
  const rings = body.rings.filter((ring) => ring.kind === 'RING');
  return {
    // The V3 catalogue lists known rings; it does not assert absence.
    state: rings.length > 0 ? 'PRESENT' : 'UNKNOWN',
    bands: rings.map((ring) => ({
      id: ring.ringKey,
      ...(ring.ringType ? { ringClass: factual(ring.ringType, source) } : {}),
      ...(typeof ring.innerRadiusKm === 'number'
        ? { innerRadiusM: factual(ring.innerRadiusKm * 1000, source) }
        : {}),
      ...(typeof ring.outerRadiusKm === 'number'
        ? { outerRadiusM: factual(ring.outerRadiusKm * 1000, source) }
        : {}),
    })),
  };
}

export function buildSystemScene(
  system: SystemDetail,
  revision: number,
  selectedBodyId?: number,
): SystemSceneContract {
  const source = 'ED-Finder V3 canonical catalogue';
  const selectableBodiesByKey = new Map(
    system.bodies
      .filter((body) => body.id !== null && Number.isSafeInteger(body.id))
      .map((body) => [body.bodyKey, body]),
  );
  const compactMoons = new Set<number>();
  const descriptors = system.bodies
    .filter(
      (body): body is typeof body & { id: number } =>
        typeof body.id === 'number' && Number.isSafeInteger(body.id),
    )
    .map((body): BodyVisualDescriptor => {
      const label = bodyTypeLabel(body.bodyType);
      const subtype = stellarPresentationSubtype(body);
      const parent = body.directParentBodyKey
        ? selectableBodiesByKey.get(body.directParentBodyKey)
        : undefined;
      // Only a retained, canonical planet parent establishes a moon. A name,
      // missing parent or body ordering never supplies that relationship.
      const isMoon =
        parent?.bodyType === 'planet' && body.bodyType === 'planet';
      if (isMoon) compactMoons.add(body.id);
      return {
        ref: { systemId64: system.id64, bodyId: body.id },
        name: body.name,
        bodyKey: body.bodyKey,
        directParentBodyKey: body.directParentBodyKey,
        isMainStar: body.isMainStar === true,
        ...(parent?.id !== null && parent?.id !== undefined
          ? { parent: { systemId64: system.id64, bodyId: parent.id } }
          : {}),
        ...(label ? { class: factual(label, source) } : {}),
        ...(body.bodyType === 'planet' && body.bodyClass != null
          ? {
              bodyClass: factual(
                body.bodyClass,
                body.bodyClassSource ?? undefined,
              ),
            }
          : {}),
        ...(body.bodyType === 'planet'
          ? { bodyFamily: bodyFamilyFromClass(body.bodyClass) }
          : {}),
        ...(subtype
          ? {
              subtype: derived(
                subtype,
                source,
                'presentation phrase derived from the catalogued spectral class',
              ),
            }
          : {}),
        ...(typeof body.radiusKm === 'number' && body.radiusKm > 0
          ? { physicalRadiusM: factual(body.radiusKm * 1000, source) }
          : {}),
        ...(typeof body.distanceFromArrivalLs === 'number' &&
        body.distanceFromArrivalLs >= 0
          ? {
              distanceFromArrivalLs: factual(
                body.distanceFromArrivalLs,
                source,
              ),
            }
          : {}),
        displayRadius: isMoon ? 0.075 : bodyDisplayRadius(body),
        orbital: {
          placement: 'DETERMINISTIC_SCHEMATIC',
          ...(positive(body.semiMajorAxisKm)
            ? { semiMajorAxisKm: factual(body.semiMajorAxisKm, source) }
            : {}),
        },
        rings: ringDescriptor(body, source),
      };
    });
  const focus: SpatialTarget =
    selectedBodyId !== undefined &&
    descriptors.some((body) => body.ref.bodyId === selectedBodyId)
      ? {
          kind: 'body',
          ref: { systemId64: system.id64, bodyId: selectedBodyId },
        }
      : { kind: 'system', systemId64: system.id64 };

  // Overview size policy (schematic, never physical): preserve the previous
  // star/planet angular sizes through the ordinary 38–60 overview range.
  // Moons use a separate 0.075-unit floor: about 3 pixels in diameter at range
  // 60 on a 900px-high viewport with Babylon's default 0.8-radian field of view.
  // Picking supplies a 6 CSS-pixel radius independently of visible geometry.
  // Beyond this overview range, zooming is necessary to inspect dense systems;
  // unbounded size inflation would make disjoint bands impossible to frame.
  // The default pitched orbital plane fits a desktop map at 1.7 times its
  // extent; the old 2.6 spherical fit left most of that viewport empty.
  const fit = (items: readonly BodyVisualDescriptor[]) =>
    Math.ceil(
      systemBodyLayout(items).reduce(
        (extent, item) =>
          Math.max(
            extent,
            (Math.hypot(item.position.x, item.position.z) + item.regionRadius) *
              1.7,
          ),
        38,
      ),
    );
  let distance = fit(descriptors);
  let bodies = descriptors;
  // A fixed ceiling prevents pathological forests from causing an expanding
  // layout/camera feedback loop. Ordinary systems converge within this budget.
  for (let iteration = 0; iteration < 24; iteration++) {
    const scale = Math.min(60, distance) / 38;
    bodies = descriptors.map((body) => ({
      ...body,
      displayRadius: compactMoons.has(body.ref.bodyId)
        ? body.displayRadius
        : body.displayRadius * scale,
    }));
    const fitted = fit(bodies);
    if (fitted <= distance) break;
    distance = fitted;
  }
  return {
    kind: 'system',
    revision,
    systemId64: system.id64,
    fidelity: bodies.length > 0 ? 'S1' : 'S0',
    camera: {
      systemId64: system.id64,
      focus,
      semanticDistance: distance,
      bearingRad: -0.85,
      pitchRad: 0.58,
      revision,
    },
    bodies,
    infrastructure: [],
    contributions: [],
  };
}

/**
 * The overview fit assumes a desktop canvas aspect; the 1.7 factor frames the
 * layout's XZ extent horizontally at this width-to-height ratio.
 */
export const SYSTEM_OVERVIEW_REFERENCE_ASPECT = 1200 / 684;

/**
 * Widen the overview distance for narrower canvases so the same layout extent
 * still fits horizontally. Wider canvases keep the contract's distance: the
 * vertical fit already governs there. Pure; the canvas applies it on load and
 * on Reset view, never to a camera the commander has moved.
 */
export function fitSystemCameraToViewport(
  camera: SystemCameraState,
  viewport: Readonly<{ width: number; height: number }>,
): SystemCameraState {
  const aspect = viewport.width / viewport.height;
  if (!Number.isFinite(aspect) || aspect <= 0) return camera;
  const factor = Math.max(1, SYSTEM_OVERVIEW_REFERENCE_ASPECT / aspect);
  if (factor === 1) return camera;
  return {
    ...camera,
    semanticDistance: Math.ceil(camera.semanticDistance * factor),
  };
}

type Position = Readonly<{ x: number; y: number; z: number }>;

export type SystemBodyLayout = Readonly<{
  body: BodyVisualDescriptor;
  parentBodyId: number | null;
  group: 'root' | 'attached' | 'unattached';
  placement: 'placed' | 'unplaced';
  positionUnknownReason?: string;
  /** Distance from this body's parent, never a physical orbital radius. */
  orbitRadius: number;
  /** Bounds the region allocated to this body and all of its descendants. */
  regionRadius: number;
  position: Position;
}>;

type LayoutNode = {
  body: BodyVisualDescriptor;
  parentBodyId: number | null;
  children: LayoutNode[];
  size: number;
  radius: number;
  orbit: number;
  exceedsRange: boolean;
  group: SystemBodyLayout['group'];
  reason?: string;
};

function positive(value: number | null | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0;
}

const compareText = (a: string, b: string): number =>
  a < b ? -1 : a > b ? 1 : 0;
const key = (body: BodyVisualDescriptor): string =>
  body.bodyKey ?? String(body.ref.bodyId);

/** Log-scaled kilometres give a transitive ordering across both sources. */
function logDistance(body: BodyVisualDescriptor): number | undefined {
  const axis = body.orbital?.semiMajorAxisKm?.value;
  if (positive(axis)) return Math.log1p(axis);
  const arrival = body.distanceFromArrivalLs?.value;
  if (!positive(arrival)) return undefined;
  const kilometres = arrival * 299_792.458;
  return Number.isFinite(kilometres)
    ? Math.log1p(kilometres)
    : Math.log(arrival) + Math.log(299_792.458);
}

function compareBodies(
  a: BodyVisualDescriptor,
  b: BodyVisualDescriptor,
): number {
  const distance = (body: BodyVisualDescriptor) => {
    const axis = body.orbital?.semiMajorAxisKm?.value;
    if (positive(axis)) return axis;
    const arrival = body.distanceFromArrivalLs?.value;
    return positive(arrival) ? arrival * 299_792.458 : undefined;
  };
  const left = distance(a);
  const right = distance(b);
  if (left !== undefined || right !== undefined) {
    if (left === undefined) return 1;
    if (right === undefined) return -1;
    // Sort original values, not rounded logarithms: even very close known
    // axes retain their order. Only extreme arrival conversions can overflow.
    const order =
      left === Infinity && right === Infinity
        ? a.distanceFromArrivalLs!.value - b.distanceFromArrivalLs!.value
        : left - right;
    return order || compareText(key(a), key(b));
  }
  return compareText(a.name, b.name) || compareText(key(a), key(b));
}

/**
 * O(n log n) forest construction and bounded, iterative layout. Canonical
 * parent keys are authoritative; absent/filtered parents and cycles never
 * acquire a guessed star. Authored scene fixtures can also use BodyRef links.
 *
 * Sibling subtrees occupy disjoint radial bands. A bottom-up pass reserves
 * each body's unchanged display radius, rings and complete descendant region.
 * Log distance adds spacing between ordered bands without crushing moons
 * inside their parent's glyph. A forward pass translates those local regions;
 * no physical phase, inclination or distance is implied by the result.
 */
export function systemBodyLayout(
  bodies: readonly BodyVisualDescriptor[],
): readonly SystemBodyLayout[] {
  const byKey = new Map(bodies.map((body) => [key(body), body]));
  const byId = new Map(bodies.map((body) => [body.ref.bodyId, body]));
  const nodes = new Map<number, LayoutNode>();
  for (const body of bodies) {
    const hasCanonicalParent = body.directParentBodyKey !== undefined;
    const parent = hasCanonicalParent
      ? body.directParentBodyKey === null
        ? undefined
        : byKey.get(body.directParentBodyKey!)
      : body.parent && body.parent.systemId64 === body.ref.systemId64
        ? byId.get(body.parent.bodyId)
        : undefined;
    const hasParent = hasCanonicalParent
      ? body.directParentBodyKey !== null
      : body.parent !== undefined;
    nodes.set(body.ref.bodyId, {
      body,
      parentBodyId: parent?.ref.bodyId ?? null,
      children: [],
      size: 1,
      radius: 0,
      orbit: 0,
      exceedsRange: false,
      group: parent ? 'attached' : hasParent ? 'unattached' : 'root',
      ...(hasParent && !parent ? { reason: 'parent unavailable' } : {}),
    });
  }

  // Each parent edge is visited once, even for a very long malformed chain.
  const visited = new Set<number>();
  for (const node of nodes.values()) {
    const path: LayoutNode[] = [];
    const pathIndex = new Map<number, number>();
    let current: LayoutNode | undefined = node;
    while (current && !visited.has(current.body.ref.bodyId)) {
      const id = current.body.ref.bodyId;
      const cycleStart = pathIndex.get(id);
      if (cycleStart !== undefined) {
        for (let index = cycleStart; index < path.length; index++) {
          const cyclic = path[index]!;
          cyclic.parentBodyId = null;
          cyclic.group = 'unattached';
          cyclic.reason = 'cyclic parent links';
        }
        break;
      }
      pathIndex.set(id, path.length);
      path.push(current);
      current =
        current.parentBodyId === null
          ? undefined
          : nodes.get(current.parentBodyId);
    }
    for (const item of path) visited.add(item.body.ref.bodyId);
  }

  const roots: LayoutNode[] = [];
  for (const node of nodes.values()) {
    const parent =
      node.parentBodyId === null ? undefined : nodes.get(node.parentBodyId);
    if (parent) parent.children.push(node);
    else roots.push(node);
    // A star/barycentre root defines a local reference origin, so a known zero
    // arrival distance is valid there. Missing distance still stays unknown.
    const isOrigin =
      !parent &&
      node.group === 'root' &&
      /^(Star|Barycentre)$/.test(node.body.class?.value ?? '') &&
      node.body.distanceFromArrivalLs?.value === 0;
    if (!node.reason && !isOrigin && logDistance(node.body) === undefined) {
      node.reason = 'orbital distance unavailable';
    }
  }
  for (const node of nodes.values())
    node.children.sort((a, b) => compareBodies(a.body, b.body));
  roots.sort(
    (a, b) =>
      Number(!!b.body.isMainStar) - Number(!!a.body.isMainStar) ||
      compareBodies(a.body, b.body),
  );

  // Match the sphere/marker and torus outer edge, including the torus's
  // minimum thickness. A marker's minimum must not enlarge every moon region.
  const footprint = (body: BodyVisualDescriptor) => {
    const node = nodes.get(body.ref.bodyId)!;
    const glyph = node.reason
      ? Math.max(0.55, body.displayRadius)
      : body.class?.value === 'Barycentre'
        ? 0.45
        : body.displayRadius;
    return Math.max(
      glyph,
      body.rings?.state === 'PRESENT'
        ? body.displayRadius * 1.6 +
            Math.max(0.06, body.displayRadius * 0.18) / 2
        : 0,
    );
  };
  const order = [...roots];
  for (let i = 0; i < order.length; i++) {
    for (const child of order[i]!.children) order.push(child);
  }
  for (let i = order.length - 1; i >= 0; i--) {
    const node = order[i]!;
    const distances = node.children
      .map((child) => logDistance(child.body))
      .filter((value): value is number => value !== undefined);
    const first = distances[0] ?? 0;
    const span = (distances.at(-1) ?? first) - first;
    let previous = first;
    let edge = footprint(node.body);
    for (const child of node.children) {
      const distance = logDistance(child.body) ?? previous;
      const logGap = span > 0 ? (0.06 * (distance - previous)) / span : 0;
      // Bands reserve only their visible subtree footprint plus a small
      // clearance. Log distance supplies ordering cues, not empty disc area.
      const gap = Math.min(footprint(node.body), child.radius) * 0.01;
      child.orbit = edge + gap + child.radius + logGap;
      edge = child.orbit + child.radius;
      previous = distance;
      node.size += child.size;
      node.exceedsRange ||= child.exceedsRange;
    }
    // Protect the renderer from pathological, exponentially deep hierarchies.
    // Their facts/links survive in an explicitly unplaced grid, never infinity.
    node.exceedsRange ||= edge > 1_000_000;
    node.radius = Math.min(edge, 1_000_000);
  }

  const result = new Map<number, SystemBodyLayout>();
  const pending: Array<{
    node: LayoutNode;
    position: Position;
    radius: number;
    orbit: number;
  }> = [];
  const placedRoots = roots.filter(
    (node) => !node.reason && !node.exceedsRange,
  );
  const unplacedRoots = roots.filter(
    (node) => !!node.reason || node.exceedsRange,
  );
  const maxFootprint = bodies.reduce(
    (max, body) => Math.max(max, footprint(body)),
    1,
  );
  const overflowStep = maxFootprint * 2 + 2;
  const radiusFor = (node: LayoutNode) =>
    node.exceedsRange
      ? Math.ceil(Math.sqrt(node.size)) * overflowStep
      : node.radius;
  const maxRootRadius = placedRoots.reduce(
    (max, node) => Math.max(max, radiusFor(node)),
    0,
  );
  function addRoots(group: LayoutNode[], unplaced: boolean) {
    const width = group.reduce((sum, node) => sum + 2 * radiusFor(node) + 6, 0);
    let edge = -width / 2;
    for (const node of group) {
      const radius = radiusFor(node);
      pending.push({
        node,
        radius,
        orbit: 0,
        position: {
          x: group.length === 1 ? 0 : edge + radius + 3,
          y: 0,
          z: unplaced ? maxRootRadius + radius + 12 : 0,
        },
      });
      edge += 2 * radius + 6;
    }
  }
  addRoots(placedRoots, false);
  addRoots(unplacedRoots, true);
  for (let index = 0; index < pending.length; index++) {
    const { node, position, radius, orbit } = pending[index]!;
    if (node.exceedsRange) {
      const subtree = [node];
      const columns = Math.ceil(Math.sqrt(node.size));
      for (let j = 0; j < subtree.length; j++) {
        const item = subtree[j]!;
        for (const child of item.children) subtree.push(child);
        result.set(item.body.ref.bodyId, {
          body: item.body,
          parentBodyId: item.parentBodyId,
          group: item.group,
          placement: 'unplaced',
          positionUnknownReason: 'hierarchy exceeds schematic range',
          orbitRadius: 0,
          regionRadius: Math.max(0.55, footprint(item.body)),
          position: {
            x: position.x + ((j % columns) - (columns - 1) / 2) * overflowStep,
            y: 0,
            z: position.z + Math.floor(j / columns) * overflowStep,
          },
        });
      }
      continue;
    }
    result.set(node.body.ref.bodyId, {
      body: node.body,
      parentBodyId: node.parentBodyId,
      group: node.group,
      placement: node.reason ? 'unplaced' : 'placed',
      ...(node.reason ? { positionUnknownReason: node.reason } : {}),
      orbitRadius: node.reason ? 0 : orbit,
      regionRadius: radius,
      position,
    });
    const children = node.children;
    const placedChildren = children.filter((child) => !child.reason);
    // Stable per-parent phase; only placed children divide the full circle.
    // Adding an unknown-distance child never re-phases known orbits.
    let hash = 2166136261;
    for (const char of key(node.body))
      hash = Math.imul(hash ^ char.charCodeAt(0), 16777619) >>> 0;
    const phase = (hash / 0x1_0000_0000) * Math.PI * 2;
    let placedIndex = 0;
    for (let childIndex = 0; childIndex < children.length; childIndex++) {
      const child = children[childIndex]!;
      const orbitRadius = child.orbit;
      // Unknown-distance children sit beyond the placed bands on a single
      // fixed westward shelf. This leaves the entire circle available for
      // known children without presenting the shelf as a second hemisphere.
      const angle = child.reason
        ? Math.PI
        : phase + (placedIndex++ / placedChildren.length) * Math.PI * 2;
      pending.push({
        node: child,
        position: {
          x: position.x + Math.cos(angle) * orbitRadius,
          y: 0,
          z: position.z + Math.sin(angle) * orbitRadius,
        },
        radius: child.radius,
        orbit: orbitRadius,
      });
    }
  }
  // Preserve input order for existing descriptor consumers; positions and
  // group membership are independent of catalogue response ordering.
  return bodies.map((body) => result.get(body.ref.bodyId)!);
}

export type SystemOrbitGuide = Readonly<{
  parentBodyId: number;
  bodyId: number;
  position: Position;
  radius: number;
}>;

/** One schematic ring at each placed child's actual layout radius. */
export function systemOrbitGuides(
  layouts: readonly SystemBodyLayout[],
): readonly SystemOrbitGuide[] {
  const byId = new Map(
    layouts.map((layout) => [layout.body.ref.bodyId, layout]),
  );
  return layouts
    .filter(
      (layout) =>
        layout.placement === 'placed' &&
        layout.parentBodyId !== null &&
        byId.has(layout.parentBodyId) &&
        layout.orbitRadius > 0,
    )
    .map((layout) => ({
      parentBodyId: layout.parentBodyId!,
      bodyId: layout.body.ref.bodyId,
      position: byId.get(layout.parentBodyId!)!.position,
      radius: layout.orbitRadius,
    }))
    .sort((a, b) => a.parentBodyId - b.parentBodyId || a.bodyId - b.bodyId);
}
