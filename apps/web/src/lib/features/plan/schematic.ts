import type { PlanFacilityV1 } from '$lib/api';
import type { PlannerTreeNode } from './locations';
import type { PlannerSite } from './model';

/** One thing along the schematic line: a star, a body or a belt. */
export interface SchematicItem {
  body_ref: string;
  name: string;
  kind: 'star' | 'body' | 'belt';
  radius_km: number | null;
  /** Moons are not drawn; the count is shown as a note. */
  moons: number;
  /** Planned or built sites here, including sites on its moons. */
  builds: number;
  /** What the dashed marker says, or null when nothing is planned here. */
  marker: string | null;
  /** True when the plan's first site (the Primary Port) is here. */
  primary: boolean;
}

type FlatNode = PlannerTreeNode & { depth: number };

/**
 * Maps any location to the drawn item it belongs to: itself at depth 0 or 1,
 * otherwise the nearest ancestor that is drawn (a moon folds into its planet).
 * A parent loop must not hang this.
 */
export function drawnRefResolver(
  tree: FlatNode[],
): (ref: string) => string | null {
  const byRef = new Map(tree.map((node) => [node.body_ref, node]));
  return (ref) => {
    const visited = new Set<string>();
    let node = byRef.get(ref);
    while (node) {
      if (node.depth <= 1) return node.body_ref;
      if (visited.has(node.body_ref)) return null;
      visited.add(node.body_ref);
      node = node.parent_ref ? byRef.get(node.parent_ref) : undefined;
    }
    return null;
  };
}

/**
 * Reduce the body tree to one left-to-right line: roots (usually the star)
 * and their direct children in recorded order. Deeper bodies fold into the
 * body they orbit, so a moon's build is marked on its planet.
 */
export function buildSchematic(
  tree: FlatNode[],
  sites: PlannerSite[],
  facilities: PlanFacilityV1[],
): SchematicItem[] {
  const drawnRef = drawnRefResolver(tree);
  const facilityNames = new Map(
    facilities.map((facility) => [facility.id, facility.name]),
  );
  const items: SchematicItem[] = tree
    .filter((node) => node.depth <= 1)
    .map((node) => ({
      body_ref: node.body_ref,
      name: node.name,
      kind:
        node.kind === 'belt'
          ? 'belt'
          : node.depth === 0 && node.body?.body_type?.toLowerCase() === 'star'
            ? 'star'
            : 'body',
      radius_km: node.radius_km,
      moons: 0,
      builds: 0,
      marker: null,
      primary: false,
    }));
  const itemByRef = new Map(items.map((item) => [item.body_ref, item]));

  for (const node of tree) {
    if (node.depth <= 1 || node.kind === 'belt') continue;
    const item = itemByRef.get(drawnRef(node.body_ref) ?? '');
    if (item) item.moons += 1;
  }

  const placed = new Map<string, PlannerSite[]>();
  sites.forEach((site, index) => {
    if (site.status === 'demolished' || site.body_ref === null) return;
    const item = itemByRef.get(drawnRef(site.body_ref) ?? '');
    if (!item) return;
    placed.set(item.body_ref, [...(placed.get(item.body_ref) ?? []), site]);
    if (index === 0) item.primary = true;
  });
  for (const [ref, here] of placed) {
    const item = itemByRef.get(ref)!;
    item.builds = here.length;
    item.marker = item.primary
      ? here.length === 1
        ? 'Primary Port'
        : `Primary Port + ${here.length - 1} more`
      : here.length === 1
        ? (facilityNames.get(here[0].facility_id) ?? here[0].facility_id)
        : `${here.length} builds`;
  }
  return items;
}
