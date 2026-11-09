import type {
  PlanBodiesV1,
  PlanFacilityV1,
  PlanSystemBeltV1,
  PlanSystemBodyV1,
} from '$lib/api';

export interface PlannerLocation {
  body_ref: string;
  name: string;
  parent_ref: string | null;
  kind: 'body' | 'belt';
  is_landable: boolean | null;
  has_rings: boolean;
  radius_km: number | null;
  body: PlanSystemBodyV1 | null;
  belt: PlanSystemBeltV1 | null;
}

export interface PlannerTreeNode extends PlannerLocation {
  children: PlannerTreeNode[];
}

export function isBeltCluster(body: PlanSystemBodyV1): boolean {
  return body.body_type?.toLowerCase().replace(/[_-]/g, ' ') === 'belt cluster';
}

function isBarycentre(body: PlanSystemBodyV1): boolean {
  return ['barycentre', 'barycenter'].includes(
    body.body_type?.toLowerCase() ?? '',
  );
}

function beltClusters(data: PlanBodiesV1, belt: PlanSystemBeltV1) {
  const prefix = `${belt.name} Cluster `;
  return data.bodies
    .flatMap((body, order) => {
      if (!isBeltCluster(body) || !body.name.startsWith(prefix)) return [];
      const suffix = body.name.slice(prefix.length);
      if (!/^\d+$/.test(suffix)) return [];
      const number = Number(suffix);
      return Number.isSafeInteger(number) ? [{ body, number, order }] : [];
    })
    .sort((a, b) => a.number - b.number || a.order - b.order);
}

/** Every reference a saved site may use for this belt: its clusters, then the fallback. */
export function beltAliases(
  data: PlanBodiesV1,
  belt: PlanSystemBeltV1,
): string[] {
  return [
    ...beltClusters(data, belt).map(({ body }) => body.body_id),
    `belt:${belt.belt_id}`,
  ];
}

/** Active belts use one current cluster; non-current belts retain saved aliases. */
export function beltBodyRefs(
  data: PlanBodiesV1,
  belt: PlanSystemBeltV1,
): string[] {
  if (belt.lifecycle_state !== 'ACTIVE') return beltAliases(data, belt);
  const representative = beltClusters(data, belt).find(
    ({ body }) => body.lifecycle_state === 'ACTIVE',
  );
  return [representative?.body.body_id ?? `belt:${belt.belt_id}`];
}

export function nonCurrentLocation(
  data: PlanBodiesV1,
  bodyRef: string | null,
): { name: string; lifecycle_state: string } | null {
  if (bodyRef === null) return null;
  // A representative cluster identifies its belt, whose lifecycle is authoritative.
  const location =
    data.belts.find(
      (belt) =>
        bodyRef === `belt:${belt.belt_id}` ||
        beltBodyRefs(data, belt).includes(bodyRef),
    ) ?? data.bodies.find((body) => body.body_id === bodyRef);
  return location && location.lifecycle_state !== 'ACTIVE'
    ? { name: location.name, lifecycle_state: location.lifecycle_state }
    : null;
}

/** One game location per belt; hidden clusters never become selectable locations. */
export function resolvePlanLocations(data: PlanBodiesV1): PlannerLocation[] {
  const bodies = new Map(data.bodies.map((body) => [body.body_id, body]));
  function visibleParent(
    parentId: string | null,
    ownId?: string,
  ): string | null {
    const visited = new Set(ownId ? [ownId] : []);
    while (parentId !== null) {
      if (visited.has(parentId)) return null;
      visited.add(parentId);
      const parent = bodies.get(parentId);
      if (!parent) return null;
      if (
        parent.lifecycle_state === 'ACTIVE' &&
        !isBarycentre(parent) &&
        !isBeltCluster(parent)
      )
        return parentId;
      parentId = parent.direct_parent_body_id;
    }
    return null;
  }

  const entries: { order: number; location: PlannerLocation }[] = [];
  data.bodies.forEach((body, order) => {
    if (
      body.lifecycle_state !== 'ACTIVE' ||
      isBarycentre(body) ||
      isBeltCluster(body)
    )
      return;
    entries.push({
      order,
      location: {
        body_ref: body.body_id,
        name: body.name,
        parent_ref: visibleParent(body.direct_parent_body_id, body.body_id),
        kind: 'body',
        is_landable: body.is_landable,
        has_rings: body.has_rings,
        radius_km: body.radius_km,
        body,
        belt: null,
      },
    });
  });
  data.belts.forEach((belt, beltIndex) => {
    if (belt.lifecycle_state !== 'ACTIVE') return;
    const clusters = beltClusters(data, belt);
    entries.push({
      // The cluster's position retains the parent's canonical child ordering.
      order: clusters.length
        ? Math.min(...clusters.map((cluster) => cluster.order))
        : data.bodies.length + beltIndex,
      location: {
        body_ref: beltBodyRefs(data, belt)[0],
        name: belt.name,
        parent_ref: visibleParent(belt.parent_body_id),
        kind: 'belt',
        is_landable: false,
        has_rings: true,
        radius_km: null,
        body: null,
        belt,
      },
    });
  });
  return entries
    .sort((a, b) => a.order - b.order)
    .map(({ location }) => location);
}

export function buildBodyTree(data: PlanBodiesV1): PlannerTreeNode[] {
  const nodes = resolvePlanLocations(data).map((location) => ({
    ...location,
    children: [] as PlannerTreeNode[],
  }));
  const byId = new Map(nodes.map((node) => [node.body_ref, node]));
  const roots: PlannerTreeNode[] = [];
  for (const node of nodes) {
    let parent = node.parent_ref ? byId.get(node.parent_ref) : undefined;
    // Malformed parent cycles must not hide rows or recurse forever.
    const visited = new Set([node.body_ref]);
    let ancestor = parent;
    while (ancestor) {
      if (visited.has(ancestor.body_ref)) {
        parent = undefined;
        break;
      }
      visited.add(ancestor.body_ref);
      ancestor = ancestor.parent_ref
        ? byId.get(ancestor.parent_ref)
        : undefined;
    }
    if (parent) parent.children.push(node);
    else roots.push(node);
  }
  return roots;
}

export function flattenBodyTree(
  tree: PlannerTreeNode[],
  depth = 0,
): (PlannerTreeNode & { depth: number })[] {
  return tree.flatMap((node) => [
    { ...node, depth },
    ...flattenBodyTree(node.children, depth + 1),
  ]);
}

export function shortBodyName(name: string, systemName: string): string {
  return name.startsWith(`${systemName} `)
    ? name.slice(systemName.length + 1)
    : name;
}

/**
 * How a build's location reads in a list: the body's short name, a note when
 * the body is retired or missing from the latest data, or "Unplaced".
 */
export function locationLabel(
  bodyRef: string | null,
  locations: PlannerLocation[],
  bodies: PlanBodiesV1 | undefined,
  systemName: string,
): string {
  const location = bodyRef
    ? locations.find((candidate) => candidate.body_ref === bodyRef)
    : undefined;
  if (location) return shortBodyName(location.name, systemName);
  const nonCurrent = bodies ? nonCurrentLocation(bodies, bodyRef) : null;
  if (nonCurrent) {
    const state =
      nonCurrent.lifecycle_state === 'RETIRED'
        ? 'retired'
        : 'missing from the latest data';
    return `${shortBodyName(nonCurrent.name, systemName)} (${state})`;
  }
  return 'Unplaced';
}

export function facilityFitsLocation(
  facility: PlanFacilityV1,
  location: PlannerLocation,
): boolean {
  const allowed = facility.allowed_location.toLowerCase();
  if (allowed.includes('surface')) return location.is_landable !== false;
  if (allowed.includes('ring')) return location.has_rings;
  return allowed.includes('orbital');
}
