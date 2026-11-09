import type { ExploreSystem } from '$lib/api/client';
import type { Id64 } from '$lib/domain/id64';
import type {
  CameraState,
  GalaxySceneContract,
  GalaxySystemPoint,
  GalaxySystemsPayload,
  SpatialContribution,
  Vec3Ly,
} from './contracts';

const finiteCoordinate = (value: number | null | undefined): value is number =>
  typeof value === 'number' && Number.isFinite(value);

export function mapExploreSystemPoint(
  system: ExploreSystem,
): GalaxySystemPoint | null {
  const coords = system.coords;
  if (
    !coords ||
    !finiteCoordinate(coords.x) ||
    !finiteCoordinate(coords.y) ||
    !finiteCoordinate(coords.z)
  ) {
    return null;
  }
  return {
    systemId64: system.id64,
    name: system.name?.trim() || `System ${system.id64}`,
    positionLy: { x: coords.x, y: coords.y, z: coords.z },
    ...((system.main_star_type || system.main_star_subtype) && {
      primaryStar: {
        ...(system.main_star_type && { type: system.main_star_type }),
        ...(system.main_star_subtype && { subtype: system.main_star_subtype }),
      },
    }),
    summary: {
      ...(finiteCoordinate(system.distance) && { distanceLy: system.distance }),
      ...(finiteCoordinate(system.population) && {
        population: system.population,
      }),
      ...(system.primaryEconomy && { primaryEconomy: system.primaryEconomy }),
      ...(system.secondaryEconomy && {
        secondaryEconomy: system.secondaryEconomy,
      }),
      ...(system.security && { security: system.security }),
      ...(system.allegiance && { allegiance: system.allegiance }),
      ...(system.government && { government: system.government }),
    },
  };
}

/** F4 selection reconstruction must not place unknown legacy coordinates on Sol. */
export function mapExploreSelectedSystemPoint(
  system: ExploreSystem,
): GalaxySystemPoint | null {
  const point = mapExploreSystemPoint(system);
  return point && hasKnownSelectionPosition(point) ? point : null;
}

function hasKnownSelectionPosition(point: GalaxySystemPoint): boolean {
  const { x, y, z } = point.positionLy;
  return (
    [x, y, z].every(finiteCoordinate) &&
    (x !== 0 || y !== 0 || z !== 0 || point.systemId64 === '10477373803')
  );
}

/** Keep an omitted selection outside the current page without duplicating a result. */
export function createExploreSelectionContribution(
  point: GalaxySystemPoint | null,
  systems: readonly ExploreSystem[],
  revision: number,
): SpatialContribution | null {
  if (
    !point ||
    !hasKnownSelectionPosition(point) ||
    systems.some(
      (system) =>
        system.id64 === point.systemId64 && mapExploreSystemPoint(system),
    )
  ) {
    return null;
  }
  const payload: GalaxySystemsPayload = { systems: [point] };
  return {
    id: 'finder-selection',
    owner: 'FINDER',
    revision,
    layers: [
      {
        // The layer kind is shared with results; the contribution owns identity.
        id: 'finder-systems',
        version: 1,
        representation: 'AUTHORITATIVE',
        payload,
        targetCount: 1,
        truncated: false,
      },
    ],
  };
}

function sceneCamera(points: readonly GalaxySystemPoint[]): {
  focusLy: Vec3Ly;
  distanceLy: number;
} {
  if (!points.length) {
    return { focusLy: { x: 0, y: 0, z: 0 }, distanceLy: 80 };
  }
  const focusLy = points.reduce(
    (sum, point) => ({
      x: sum.x + point.positionLy.x / points.length,
      y: sum.y + point.positionLy.y / points.length,
      z: sum.z + point.positionLy.z / points.length,
    }),
    { x: 0, y: 0, z: 0 },
  );
  const radius = points.reduce(
    (largest, point) =>
      Math.max(
        largest,
        Math.hypot(
          point.positionLy.x - focusLy.x,
          point.positionLy.y - focusLy.y,
          point.positionLy.z - focusLy.z,
        ),
      ),
    0,
  );
  return { focusLy, distanceLy: Math.max(30, radius * 2.4) };
}

export type ExploreGalaxySceneContext = Readonly<{
  /** Stable while only selection changes, so Babylon can patch in place. */
  finderRevision?: number;
  finderContribution?: SpatialContribution;
  camera?: CameraState | null;
  spatialContributions?: readonly SpatialContribution[];
  selectedRegionId?: string | null;
  /** F4-only last known or exact-system-resolved selection, independent of the page. */
  selectedPoint?: GalaxySystemPoint | null;
  /** Stable while only camera or region selection changes, like finderContribution. */
  selectionContribution?: SpatialContribution | null;
}>;

export function createExploreFinderContribution(
  systems: readonly ExploreSystem[],
  revision: number,
): SpatialContribution {
  const points = systems.flatMap((system) => {
    const point = mapExploreSystemPoint(system);
    return point ? [point] : [];
  });
  const payload: GalaxySystemsPayload = { systems: points };
  return {
    id: 'finder-results',
    owner: 'FINDER',
    revision,
    layers: [
      {
        id: 'finder-systems',
        version: 1,
        representation: 'AUTHORITATIVE',
        payload,
        targetCount: points.length,
        truncated: false,
      },
    ],
  };
}

/** Map typed API truth into the renderer-neutral Finder contribution. */
export function buildExploreGalaxyScene(
  systems: readonly ExploreSystem[],
  selectedSystemId64: Id64 | null,
  revision: number,
  context: ExploreGalaxySceneContext = {},
): GalaxySceneContract {
  const points = systems.flatMap((system) => {
    const point = mapExploreSystemPoint(system);
    return point ? [point] : [];
  });
  const initialCamera = sceneCamera(points);
  const finderRevision = context.finderRevision ?? revision;
  const finderContribution =
    context.finderContribution ??
    createExploreFinderContribution(systems, finderRevision);
  const selectionContribution =
    context.selectedPoint?.systemId64 === selectedSystemId64
      ? context.selectionContribution === undefined
        ? createExploreSelectionContribution(
            context.selectedPoint,
            systems,
            revision,
          )
        : context.selectionContribution
      : null;
  return {
    kind: 'galaxy',
    revision,
    camera: context.camera ?? {
      ...initialCamera,
      bearingRad: 0,
      pitchRad: 0.55,
      projection: 'perspective',
      revision: finderRevision,
    },
    selection: [
      ...(selectedSystemId64
        ? [{ kind: 'system', systemId64: selectedSystemId64 } as const]
        : []),
      ...(context.selectedRegionId
        ? [{ kind: 'region', id: context.selectedRegionId } as const]
        : []),
    ],
    // Without an F4 selection this is the original main composition, without
    // allocating or scanning an extra selection array.
    contributions: selectionContribution
      ? [
          finderContribution,
          selectionContribution,
          ...(context.spatialContributions ?? []),
        ]
      : [finderContribution, ...(context.spatialContributions ?? [])],
  };
}
