import type {
  FacilityTemplate,
  SimulateBuildPlacement,
  SystemBody,
} from '@ed-finder/api-client/types';
import { bodyIdKey } from './bodyId';
import { compareBodiesByHierarchy } from './bodyHierarchy';

export interface TopologyBodyNode {
  body: SystemBody;
  id: string;
  depth: number;
}

export interface ProjectedPlacementItem {
  index: number;
  placement: SimulateBuildPlacement;
  template?: FacilityTemplate;
}

export type TopologySlotLaneKind = 'orbital' | 'ground' | 'unknown';
export type TopologySlotCellTone = 'planned' | 'projected' | 'empty';

export interface TopologySlotCell {
  key: string;
  label: string;
  tone: TopologySlotCellTone;
}

export function placementLaneKind(template?: FacilityTemplate): TopologySlotLaneKind {
  const value = (template?.allowed_location ?? '').toLowerCase();
  const hasOrbital = value.includes('orbit');
  const hasGround = value.includes('surface') || value.includes('ground');
  if (hasOrbital && !hasGround) return 'orbital';
  if (hasGround && !hasOrbital) return 'ground';
  return 'unknown';
}

export function compactFacilityName(value: string): string {
  const clean = value.trim();
  if (!clean) return '';
  const parts = clean.split(/\s+/);
  if (parts.length === 1) return parts[0].slice(0, 6);
  return parts[0].slice(0, 5);
}

export function buildSlotLaneCells(
  laneKey: string,
  capacity: number,
  planned: string[],
  projected: string[],
): TopologySlotCell[] {
  return Array.from({ length: Math.max(0, capacity) }, (_unused, index) => {
    if (index < planned.length) {
      return {
        key: `planned-${laneKey}-${index}`,
        label: compactFacilityName(planned[index] ?? ''),
        tone: 'planned' as const,
      };
    }
    const projectedIndex = index - planned.length;
    if (projectedIndex >= 0 && projectedIndex < projected.length) {
      return {
        key: `projected-${laneKey}-${index}`,
        label: compactFacilityName(projected[projectedIndex] ?? ''),
        tone: 'projected' as const,
      };
    }
    return {
      key: `empty-${laneKey}-${index}`,
      label: '',
      tone: 'empty' as const,
    };
  });
}

export function bucketProjectedPlacements(
  placements: SimulateBuildPlacement[],
  templates: FacilityTemplate[],
  bodies: SystemBody[],
): Map<string, ProjectedPlacementItem[]> {
  const bodyIds = new Set(
    bodies
      .filter((body) => body.id != null)
      .map((body) => bodyIdKey(body.id)),
  );
  const templatesById = new Map(templates.map((template) => [template.id, template]));
  const buckets = new Map<string, ProjectedPlacementItem[]>();
  placements.forEach((placement, index) => {
    const bodyId = placement.local_body_id != null ? bodyIdKey(placement.local_body_id) : null;
    if (!bodyId || !bodyIds.has(bodyId)) return;
    const list = buckets.get(bodyId) ?? [];
    list.push({
      index,
      placement,
      template: templatesById.get(placement.facility_template_id),
    });
    buckets.set(bodyId, list);
  });
  return buckets;
}

export function buildBodyNodes(
  bodies: SystemBody[],
  systemName?: string | null,
): TopologyBodyNode[] {
  const withIds = bodies
    .filter((body) => body.id != null)
    .map((body) => ({ body, id: bodyIdKey(body.id) }));
  const knownIds = new Set(withIds.map((item) => item.id));
  const children = new Map<string, Array<{ body: SystemBody; id: string }>>();
  const roots: Array<{ body: SystemBody; id: string }> = [];

  for (const item of withIds) {
    const parentId = bodyParentId(item.body);
    if (parentId && knownIds.has(parentId)) {
      const list = children.get(parentId) ?? [];
      list.push(item);
      children.set(parentId, list);
    } else {
      roots.push(item);
    }
  }

  const nodes: TopologyBodyNode[] = [];
  const visit = (item: { body: SystemBody; id: string }, depth: number) => {
    nodes.push({ ...item, depth });
    for (const child of sortBodies(children.get(item.id) ?? [], systemName)) {
      visit(child, depth + 1);
    }
  };

  for (const root of sortBodies(roots, systemName)) {
    visit(root, 0);
  }
  return nodes;
}

export function bodyParentId(body: SystemBody): string | null {
  const raw = body.parent_body_id
    ?? body.parentBodyId
    ?? body.parent_id
    ?? body.parentId
    ?? body.orbiting_body_id
    ?? body.orbitingBodyId
    ?? null;
  if (typeof raw === 'number' || typeof raw === 'string') return bodyIdKey(raw);
  return null;
}

export function sortBodies<T extends { body: SystemBody; id: string }>(
  items: T[],
  systemName?: string | null,
): T[] {
  return items
    .map((item, index) => ({ item, index }))
    .sort((a, b) => {
      const rank = (body: SystemBody) => body.body_type === 'Star' ? 0 : body.body_type === 'Planet' ? 1 : 2;
      if (rank(a.item.body) !== rank(b.item.body)) return rank(a.item.body) - rank(b.item.body);
      return compareBodiesByHierarchy(a.item.body, b.item.body, systemName) || a.index - b.index;
    })
    .map(({ item }) => item);
}

export function bodyIcon(body: SystemBody): string {
  if (body.body_type === 'Star') return 'S';
  if (body.body_type === 'Planet' && bodyParentId(body)) return 'M';
  if (body.body_type === 'Planet') return 'P';
  return 'B';
}

export function compactBodyKind(body: SystemBody): string {
  const type = body.body_type ?? 'Body';
  const subtype = body.subtype?.replace(/\bworld\b/i, '').trim();
  const kind = subtype || type;
  const flags = [
    body.is_landable ? 'landable' : null,
    body.is_water_world ? 'water' : null,
    body.is_terraformable ? 'terraformable' : null,
  ].filter(Boolean);
  return flags.length > 0 ? `${kind} / ${flags.slice(0, 1).join(', ')}` : kind;
}
