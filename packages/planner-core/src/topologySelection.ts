import type {
  FacilityTemplate,
  SimulateBuildPlacement,
  SlotPredictionResponse,
  SystemBody,
  SystemDetail,
} from '@ed-finder/api-client/types';
import {
  bodyDisplayName,
  getBodyGroupWarnings,
  getPlacementWarnings,
  type BodyGroup,
  type GroupedPlacement,
} from './buildPlanLayout';
import { bodyIdKey } from './bodyId';
import { systemBodyData } from './slotCapacity';
import type { BodyPlannerLane } from './plannerTypes';

export type TopologySelection =
  | { type: 'system' }
  | { type: 'body'; bodyId: string }
  | { type: 'placement'; placementIndex: number }
  | { type: 'projected-placement'; placementIndex: number }
  | { type: 'group'; groupKey: 'unassigned' | 'unknown' };

export interface TopologyPlanSnapshot {
  placements: SimulateBuildPlacement[];
  templates: FacilityTemplate[];
  targetArchetype: string;
  slotPredictions?: SlotPredictionResponse | null;
  placementLaneHints?: Record<number, BodyPlannerLane>;
  projection?: {
    candidateId: string;
    label: string;
    placements: SimulateBuildPlacement[];
    placementLaneHints?: Record<number, BodyPlannerLane>;
  } | null;
}

export interface TopologySelectionContext {
  label: string;
  kind: string;
  placementCount: number;
  warningCount: number;
  architectStatus: string;
  detail: string;
}

export interface PlacementBucket {
  knownByBody: Map<string, GroupedPlacement[]>;
  unknown: GroupedPlacement[];
  unassigned: GroupedPlacement[];
}

export function describeTopologySelection(
  selection: TopologySelection,
  system: SystemDetail,
  snapshot: TopologyPlanSnapshot,
): TopologySelectionContext {
  const bodies = systemBodyData(system);
  const buckets = bucketPlacements(snapshot.placements, snapshot.templates, bodies);
  const bodyById = new Map(
    bodies
      .filter((body) => body.id != null)
      .map((body) => [bodyIdKey(body.id), body]),
  );

  if (selection.type === 'body') {
    const bodyId = bodyIdKey(selection.bodyId);
    const body = bodyById.get(bodyId);
    const placements = buckets.knownByBody.get(bodyId) ?? [];
    const warnings = body ? getBodyGroupWarnings({ key: bodyId, body, placements }) : [];
    return {
      label: body ? bodyDisplayName(body) : 'Unknown body',
      kind: body?.subtype ?? body?.body_type ?? 'Body',
      placementCount: placements.length,
      warningCount: warnings.length,
      architectStatus: 'Architect flag not recorded',
      detail: placements.length > 0
        ? 'Body selected. Review or add structures in the inline canvas expansion.'
        : 'Body selected. Add the first structure in the inline canvas expansion.',
    };
  }

  if (selection.type === 'placement') {
    const item = allPlacements(buckets).find((candidate) => candidate.index === selection.placementIndex);
    const body = item?.bodyId ? bodyById.get(item.bodyId) ?? null : null;
    const warnings = item ? getPlacementWarnings(item, body) : [];
    return {
      label: item?.template?.name ?? item?.placement.facility_template_id ?? 'Selected placement',
      kind: item?.placement.is_primary_port ? 'Primary-port placement' : 'Planned placement',
      placementCount: item ? 1 : 0,
      warningCount: warnings.length,
      architectStatus: item?.placement.is_primary_port ? 'Primary-port placement planned; Architect flag not recorded' : 'Architect flag not recorded',
      detail: body ? `Assigned to ${bodyDisplayName(body)}.` : item?.hasUnknownBody ? 'Assigned body is not in the loaded body list.' : 'No body assigned yet.',
    };
  }

  if (selection.type === 'projected-placement') {
    const placement = snapshot.projection?.placements[selection.placementIndex];
    const template = placement
      ? snapshot.templates.find((candidate) => candidate.id === placement.facility_template_id)
      : undefined;
    const bodyId = placement?.local_body_id != null ? bodyIdKey(placement.local_body_id) : null;
    const body = bodyId ? bodyById.get(bodyId) ?? null : null;
    return {
      label: template?.name ?? placement?.facility_template_id ?? 'Projected placement',
      kind: 'Projected Suggested Build placement',
      placementCount: placement ? 1 : 0,
      warningCount: placement && !body ? 1 : 0,
      architectStatus: 'Projected only; not loaded into the Build Plan',
      detail: body ? `Ghost structure projected for ${bodyDisplayName(body)}.` : 'Projected structure has no matched body.',
    };
  }

  if (selection.type === 'group') {
    const placements = selection.groupKey === 'unknown' ? buckets.unknown : buckets.unassigned;
    return {
      label: selection.groupKey === 'unknown' ? 'Unknown / unmatched body' : 'Unassigned placements',
      kind: selection.groupKey === 'unknown' ? 'Needs body match' : 'Needs assignment',
      placementCount: placements.length,
      warningCount: placements.length,
      architectStatus: 'Architect flag not recorded',
      detail: selection.groupKey === 'unknown'
        ? 'Placement body references do not match the loaded system bodies.'
        : 'Placements are not assigned to a body yet.',
    };
  }

  return {
    label: system.name || 'System',
    kind: 'System root',
    placementCount: snapshot.placements.length,
    warningCount: countWorkspaceWarnings(snapshot, bodies),
    architectStatus: 'Architect flag not recorded',
    detail: 'Select a body to inspect local suitability and planned structures.',
  };
}

export function bucketPlacements(
  placements: SimulateBuildPlacement[],
  templates: FacilityTemplate[],
  bodies: SystemBody[],
): PlacementBucket {
  const templatesById = new Map(templates.map((template) => [template.id, template]));
  const bodyIds = new Set(
    bodies
      .filter((body) => body.id != null)
      .map((body) => bodyIdKey(body.id)),
  );
  const knownByBody = new Map<string, GroupedPlacement[]>();
  const unknown: GroupedPlacement[] = [];
  const unassigned: GroupedPlacement[] = [];

  placements.forEach((placement, index) => {
    const bodyId = bodyIdKey(placement.local_body_id);
    const item: GroupedPlacement = {
      placement,
      index,
      template: templatesById.get(placement.facility_template_id),
      bodyId: bodyId || undefined,
      hasUnknownBody: Boolean(bodyId && !bodyIds.has(bodyId)),
    };

    if (!bodyId) {
      unassigned.push(item);
      return;
    }
    if (!bodyIds.has(bodyId)) {
      unknown.push(item);
      return;
    }
    const list = knownByBody.get(bodyId) ?? [];
    list.push(item);
    knownByBody.set(bodyId, list);
  });

  return { knownByBody, unknown, unassigned };
}

function allPlacements(buckets: PlacementBucket): GroupedPlacement[] {
  return [
    ...Array.from(buckets.knownByBody.values()).flat(),
    ...buckets.unknown,
    ...buckets.unassigned,
  ];
}

function countWorkspaceWarnings(snapshot: TopologyPlanSnapshot, bodies: SystemBody[]): number {
  const buckets = bucketPlacements(snapshot.placements, snapshot.templates, bodies);
  const bodyById = new Map(
    bodies
      .filter((body) => body.id != null)
      .map((body) => [bodyIdKey(body.id), body]),
  );
  const bodyWarnings = Array.from(buckets.knownByBody.entries()).reduce((count, [bodyId, placements]) => {
    const body = bodyById.get(bodyId) ?? null;
    return count + getBodyGroupWarnings({ key: bodyId, body, placements } as BodyGroup).length;
  }, 0);
  return bodyWarnings + buckets.unknown.length + buckets.unassigned.length;
}
