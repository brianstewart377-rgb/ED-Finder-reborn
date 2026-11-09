import { compareBodiesByHierarchy } from './bodyHierarchy';
import type { BodySlotPrediction, FacilityTemplate, SimulateBuildPlacement, SystemBody, SystemDetail } from '@ed-finder/api-client/types';
import {
  bodyDisplayName,
  bodyTags,
  compactBodyDisplayName,
} from './buildPlanLayout';
import { bodyIdKey, sameBodyId } from './bodyId';
import type { TopologyPlanSnapshot } from './topologySelection';
import type { BodyPlannerLane } from './plannerTypes';
import { formatShare } from './plannerCanvasPresentation';
import { bodyParentId } from './topologyModel';
import {
  buildBodyDataSlotEstimateMap,
  resolveSlotCapacity,
  systemBodyData,
} from './slotCapacity';
import {
  buildPlanningEconomyLedger,
  normalisePlanningEconomy,
  type PlanningEconomyLedger,
} from './planningEconomy';
import { calculateStationBaselineEconomy, describeStationBaselineEconomy } from './stationBaselineEconomy';
import {
  existingStructureDisplayType,
  resolveExistingInfrastructure,
  type ExistingStructure,
} from './existingInfrastructure';
import type {
  ProjectionComparisonSummary,
  PlannerEconomySegment,
  PlannerCanvasLane,
  PlannerLaneOccupancySummary,
  PlannerCanvasRow,
  PlannerStructureSlot,
} from './plannerCanvasTypes';
export type {
  ProjectionComparisonSummary,
  PlannerEconomySegment,
  PlannerCanvasLane,
  PlannerLaneOccupancySummary,
  PlannerCanvasRow,
  PlannerSlotKind,
  PlannerStructureSlot,
  ProjectionEconomyDelta,
} from './plannerCanvasTypes';
import {
  contextualEconomyLabel,
  laneDisabledReason,
  missingPrerequisitesForPlacement,
  placementLaneForTemplate,
  templateDisplayName,
} from './structurePlanning';

interface BgsTelemetryStat {
  id: string;
  label: string;
  field: string;
  value: number;
}

const BGS_TELEMETRY_FIELDS: Array<Omit<BgsTelemetryStat, 'value'>> = [
  { id: 'population', label: 'Population', field: 'population' },
  { id: 'max-population', label: 'Max population', field: 'max_population' },
  { id: 'security', label: 'Security', field: 'security' },
  { id: 'tech-level', label: 'Tech level', field: 'tech_level' },
  { id: 'wealth', label: 'Wealth', field: 'wealth' },
  { id: 'standard-of-living', label: 'Standard of living', field: 'standard_of_living' },
  { id: 'development-level', label: 'Development level', field: 'development_level' },
];

interface BodyNode {
  body: SystemBody;
  id: string;
  children: BodyNode[];
}

interface FlatBodyNode {
  body: SystemBody;
  id: string;
  depth: number;
  guide: boolean[];
  isLast: boolean;
}

interface StructureBucketItem {
  kind: 'placement';
  placement: SimulateBuildPlacement;
  template?: FacilityTemplate;
  index: number;
  projected: boolean;
  warningLabels: string[];
}

interface ExistingStructureBucketItem {
  kind: 'existing';
  structure: ExistingStructure;
  index: number;
  projected: false;
  warningLabels: string[];
}

type PlannerStructureBucketItem = StructureBucketItem | ExistingStructureBucketItem;

const BODY_MARKER_COLORS: Record<string, { fill: string; ring: string; size: string }> = {
  star: { fill: 'radial-gradient(circle at 35% 30%, #ffd18c, #ff9f1a 55%, #9a4d00)', ring: 'rgba(255, 122, 20, 0.58)', size: 'h-8 w-8' },
  gas: { fill: 'radial-gradient(circle at 35% 30%, #ff8fb8, #d9467d 55%, #6d1539)', ring: 'rgba(248, 113, 113, 0.5)', size: 'h-7 w-7' },
  earth: { fill: 'radial-gradient(circle at 35% 30%, #98f5c5, #38bdf8 45%, #0f766e 78%)', ring: 'rgba(74, 222, 128, 0.5)', size: 'h-5 w-5' },
  rock: { fill: 'radial-gradient(circle at 35% 30%, #9ca3af, #525861 60%, #22262b)', ring: 'rgba(200, 204, 209, 0.45)', size: 'h-5 w-5' },
  moon: { fill: 'radial-gradient(circle at 35% 30%, #d1d5db, #7c8189 60%, #31363d)', ring: 'rgba(200, 204, 209, 0.38)', size: 'h-4 w-4' },
};

export function buildPlannerCanvasRows(system: SystemDetail, snapshot: TopologyPlanSnapshot): PlannerCanvasRow[] {
  const bodies = systemBodyData(system);
  const existingResolution = resolveExistingInfrastructure(system);
  const bodyDataSlotEstimates = buildBodyDataSlotEstimateMap(system, snapshot.slotPredictions?.predictions);
  const predictionsByBodyId = new Map(
    (snapshot.slotPredictions?.predictions ?? []).map((prediction) => [bodyIdKey(prediction.body_id), prediction]),
  );
  const templatesById = new Map(snapshot.templates.map((template) => [template.id, template]));
  const bodyById = new Map(
    bodies
      .filter((body) => body.id != null)
      .map((body) => [bodyIdKey(body.id), body]),
  );
  const plannedByBody = bucketStructures(snapshot.placements, templatesById, bodyById, false, snapshot.placementLaneHints);
  const projectedByBody = bucketStructures(snapshot.projection?.placements ?? [], templatesById, bodyById, true, snapshot.projection?.placementLaneHints);
  const projectedBodyIds = new Set(Array.from(projectedByBody.keys()));

  return flattenBodyNodes(bodies, system.name).map((node) => {
    const prediction = predictionsByBodyId.get(node.id) ?? null;
    const bodyDataSlotEstimate = bodyDataSlotEstimates.get(node.id) ?? null;
    const planned = plannedByBody.get(node.id) ?? emptyStructureBuckets();
    const projected = projectedByBody.get(node.id) ?? emptyStructureBuckets();
    const existing = existingResolution.byBodyId.get(node.id) ?? emptyExistingStructureBuckets();
    const existingOrbital = existing.orbital.map((structure, index): ExistingStructureBucketItem => ({
      kind: 'existing',
      structure,
      index,
      projected: false,
      warningLabels: [],
    }));
    const existingGround = existing.surface.map((structure, index): ExistingStructureBucketItem => ({
      kind: 'existing',
      structure,
      index,
      projected: false,
      warningLabels: [],
    }));
    const orbitalStructures = [...existingOrbital, ...planned.orbital, ...projected.orbital];
    const groundStructures = [...existingGround, ...planned.ground, ...projected.ground];
    const unassignedStructures = [...planned.unassigned, ...projected.unassigned];
    const orbitalSlotCapacity = resolveSlotCapacity(node.body, prediction, 'orbital', bodyDataSlotEstimate);
    const groundSlotCapacity = resolveSlotCapacity(node.body, prediction, 'surface', bodyDataSlotEstimate);
    const orbitalCapacity = orbitalSlotCapacity.value;
    const groundCapacity = groundSlotCapacity.value;
    const bodyLedger = buildPlanningEconomyLedger({
      placements: [...planned.orbital, ...planned.ground, ...planned.unassigned].map((item) => item.placement),
      projectedPlacements: [...projected.orbital, ...projected.ground, ...projected.unassigned].map((item) => item.placement),
      templates: snapshot.templates,
    });

    return {
      id: node.id,
      body: node.body,
      depth: node.depth,
      guide: node.guide,
      isLast: node.isLast,
      displayName: bodyDisplayName(node.body),
      compactName: compactBodyDisplayName(node.body, system.name),
      bodyKind: bodyKind(node.body),
      bodyTags: bodyTags(node.body),
      orbitalCapacity,
      groundCapacity,
      orbitalCapacityEstimated: orbitalSlotCapacity.estimated,
      groundCapacityEstimated: groundSlotCapacity.estimated,
      ...(() => {
        const orbitalSlots = buildLaneSlots(node.id, 'orbital', orbitalCapacity, orbitalStructures, node.body, system);
        const groundSlots = buildLaneSlots(node.id, 'ground', groundCapacity, groundStructures, node.body, system);
        const unassignedSlots = unassignedStructures.map((item, index) => structureSlot(node.id, 'unassigned', item, index, node.body, system));
        const overflowCount = [...orbitalSlots, ...groundSlots].filter((slot) => slot.kind === 'overflow').length;
        const placementWarningCount = [...planned.orbital, ...planned.ground, ...planned.unassigned]
          .filter((item) => item.warningLabels.length > 0).length;
        const existingCount = existing.orbital.length + existing.surface.length;
        const inferredExistingCount = [...existing.orbital, ...existing.surface]
          .filter((structure) => structure.association_status === 'inferred').length;
        const plannedCount = planned.orbital.length + planned.ground.length + planned.unassigned.length;
        const projectedCount = projected.orbital.length + projected.ground.length + projected.unassigned.length;
        const emptySlotCount = [...orbitalSlots, ...groundSlots].filter((slot) => slot.kind === 'empty').length;
        const orbitalOccupancy = buildLaneOccupancy(orbitalCapacity, existing.orbital, planned.orbital.length, projected.orbital.length);
        const groundOccupancy = buildLaneOccupancy(groundCapacity, existing.surface, planned.ground.length, projected.ground.length);
        return {
          orbitalSlots,
          groundSlots,
          unassignedSlots,
          bodyEconomy: bodyLedger,
          projected: projectedBodyIds.has(node.id),
          existingCount,
          inferredExistingCount,
          plannedCount,
          projectedCount,
          emptySlotCount,
          orbitalOccupancy,
          groundOccupancy,
          orbitalAddDisabledReason: addDisabledReasonForLane(node.body, 'orbital', orbitalCapacity, existing.orbital.length, planned.orbital.length),
          groundAddDisabledReason: addDisabledReasonForLane(node.body, 'surface', groundCapacity, existing.surface.length, planned.ground.length),
          warningCount: countRowWarnings(node.body, prediction, bodyLedger, overflowCount) + placementWarningCount,
        };
      })(),
    };
  });
}

export interface PlannerCanvasOccupancySummary {
  existingCount: number;
  inferredExistingCount: number;
  plannedCount: number;
  projectedCount: number;
  emptySlotCount: number;
  unresolvedExistingCount: number;
}

export interface PlannerLaneCapacityState {
  capacity: number | null;
  existingCount: number;
  plannedCount: number;
  projectedCount: number;
  remaining: number | null;
  disabledReason: string | null;
}

export function buildPlannerCanvasOccupancySummary(system: SystemDetail, snapshot: TopologyPlanSnapshot): PlannerCanvasOccupancySummary {
  const rows = buildPlannerCanvasRows(system, snapshot);
  const unresolvedExistingCount = resolveExistingInfrastructure(system).unresolved.length;
  return summarizePlannerCanvasRows(rows, unresolvedExistingCount);
}

export function getPlannerLaneCapacityState(
  system: SystemDetail,
  snapshot: TopologyPlanSnapshot,
  bodyId: string,
  lane: BodyPlannerLane,
): PlannerLaneCapacityState {
  const bodies = systemBodyData(system);
  const body = bodies.find((candidate) => sameBodyId(candidate.id, bodyId));
  if (!body) {
    return {
      capacity: null,
      existingCount: 0,
      plannedCount: 0,
      projectedCount: 0,
      remaining: null,
      disabledReason: 'Selected body is no longer available.',
    };
  }

  const bodyKey = bodyIdKey(body.id);
  const bodyDataSlotEstimates = buildBodyDataSlotEstimateMap(system, snapshot.slotPredictions?.predictions);
  const prediction = (snapshot.slotPredictions?.predictions ?? []).find((candidate) => sameBodyId(candidate.body_id, body.id)) ?? null;
  const capacity = resolveSlotCapacity(
    body,
    prediction,
    lane,
    bodyDataSlotEstimates.get(bodyKey) ?? null,
  ).value;
  const bodyById = new Map(bodies.filter((candidate) => candidate.id != null).map((candidate) => [bodyIdKey(candidate.id), candidate]));
  const templatesById = new Map(snapshot.templates.map((template) => [template.id, template]));
  const existingBuckets = resolveExistingInfrastructure(system).byBodyId.get(bodyKey) ?? emptyExistingStructureBuckets();
  const existingCount = lane === 'orbital' ? existingBuckets.orbital.length : existingBuckets.surface.length;
  const plannedCount = countPlacementsInLane(snapshot.placements, templatesById, bodyById, bodyKey, lane, snapshot.placementLaneHints);
  const projectedCount = countPlacementsInLane(snapshot.projection?.placements ?? [], templatesById, bodyById, bodyKey, lane, snapshot.projection?.placementLaneHints);
  const remaining = capacity == null ? null : Math.max(0, capacity - existingCount - plannedCount);

  return {
    capacity,
    existingCount,
    plannedCount,
    projectedCount,
    remaining,
    disabledReason: addDisabledReasonForLane(body, lane, capacity, existingCount, plannedCount),
  };
}

export function summarizePlannerCanvasRows(rows: PlannerCanvasRow[], unresolvedExistingCount: number): PlannerCanvasOccupancySummary {
  return rows.reduce<PlannerCanvasOccupancySummary>((summary, row) => ({
    existingCount: summary.existingCount + row.existingCount,
    inferredExistingCount: summary.inferredExistingCount + row.inferredExistingCount,
    plannedCount: summary.plannedCount + row.plannedCount,
    projectedCount: summary.projectedCount + row.projectedCount,
    emptySlotCount: summary.emptySlotCount + row.emptySlotCount,
    unresolvedExistingCount,
  }), {
    existingCount: 0,
    inferredExistingCount: 0,
    plannedCount: 0,
    projectedCount: 0,
    emptySlotCount: 0,
    unresolvedExistingCount,
  });
}

function buildLaneOccupancy(
  capacity: number | null,
  existingStructures: ExistingStructure[],
  plannedCount: number,
  projectedCount: number,
): PlannerLaneOccupancySummary {
  const existingCount = existingStructures.length;
  const inferredExistingCount = existingStructures
    .filter((structure) => structure.association_status === 'inferred').length;
  const remainingForPlan = capacity == null ? null : Math.max(0, capacity - existingCount - plannedCount);
  const projectedOverflowCount = capacity == null ? 0 : Math.max(0, existingCount + plannedCount + projectedCount - capacity);
  return {
    capacity,
    existingCount,
    inferredExistingCount,
    plannedCount,
    projectedCount,
    remainingForPlan,
    projectedOverflowCount,
  };
}

function countPlacementsInLane(
  placements: SimulateBuildPlacement[],
  templatesById: Map<string, FacilityTemplate>,
  bodyById: Map<string, SystemBody>,
  targetBodyId: string,
  lane: BodyPlannerLane,
  laneHints: Record<number, BodyPlannerLane> = {},
): number {
  return placements.reduce((count, placement, index) => {
    const bodyId = placementBodyId(placement);
    if (!bodyId || bodyId !== targetBodyId) return count;
    const template = templatesById.get(placement.facility_template_id);
    const assignedLane = placementLaneForTemplate(template, bodyById.get(bodyId), laneHints[index]);
    return assignedLane === lane ? count + 1 : count;
  }, 0);
}

function addDisabledReasonForLane(
  body: SystemBody,
  lane: BodyPlannerLane,
  capacity: number | null,
  existingCount: number,
  plannedCount: number,
): string | null {
  const physicalReason = laneDisabledReason(body, lane);
  if (physicalReason) return physicalReason;
  if (capacity == null) return null;
  if (capacity <= 0) {
    return lane === 'orbital' ? 'No orbital slots predicted.' : 'No surface slots predicted.';
  }
  if (existingCount + plannedCount >= capacity) {
    const summary = `${capacity} slots, ${existingCount} existing, ${plannedCount} planned`;
    return lane === 'orbital'
      ? `No empty orbital slots (${summary}).`
      : `All surface slots occupied (${summary}).`;
  }
  return null;
}

export function buildPlannerTelemetryStats(snapshot: TopologyPlanSnapshot): BgsTelemetryStat[] {
  const templatesById = new Map(snapshot.templates.map((template) => [template.id, template]));
  return BGS_TELEMETRY_FIELDS.map((stat) => ({
    ...stat,
    value: snapshot.placements.reduce((sum, placement) => (
      sum + readTemplateStat(templatesById.get(placement.facility_template_id), stat.field)
    ), 0),
  }));
}

function readTemplateStat(template: FacilityTemplate | undefined, field: string): number {
  if (!template) return 0;
  const direct = numericValue((template as unknown as Record<string, unknown>)[field]);
  if (direct != null) return direct;
  const effects = (template.stat_effects ?? (template as unknown as { statEffects?: Record<string, unknown> }).statEffects) as
    | Record<string, unknown>
    | undefined;
  return numericValue(effects?.[field]) ?? 0;
}

export function numericValue(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}


export function buildProjectionComparison(
  system: SystemDetail,
  snapshot: TopologyPlanSnapshot,
  economyLedger: PlanningEconomyLedger,
  rows = buildPlannerCanvasRows(system, snapshot),
): ProjectionComparisonSummary {
  const bodies = systemBodyData(system);
  const bodyById = new Map(
    bodies
      .filter((body) => body.id != null)
      .map((body) => [bodyIdKey(body.id), body]),
  );
  const templatesById = new Map(snapshot.templates.map((template) => [template.id, template]));
  const plannedBodyIds = uniquePlacementBodyIds(snapshot.placements, bodies);
  const projectedBodyIds = uniquePlacementBodyIds(snapshot.projection?.placements ?? [], bodies);
  const sharedBodyIds = projectedBodyIds.filter((id) => plannedBodyIds.includes(id));
  const newBodyIds = projectedBodyIds.filter((id) => !plannedBodyIds.includes(id));
  const plannedOnlyBodyIds = plannedBodyIds.filter((id) => !projectedBodyIds.includes(id));
  const laneCounts = (snapshot.projection?.placements ?? []).reduce((counts, placement) => {
    const bodyId = placementBodyId(placement);
    const template = templatesById.get(placement.facility_template_id);
    const lane = plannerCanvasLaneForPlacement(
      template,
      bodyId ? bodyById.get(bodyId) : undefined,
      snapshot.projection?.placementLaneHints?.[counts.index],
    );
    if (lane === 'orbital') counts.orbital += 1;
    else if (lane === 'ground') counts.ground += 1;
    else counts.unknown += 1;
    counts.index += 1;
    return counts;
  }, { orbital: 0, ground: 0, unknown: 0, index: 0 });
  const slotOverflowCount = rows.reduce((sum, row) => (
    sum
      + row.orbitalSlots.filter((slot) => slot.kind === 'overflow').length
      + row.groundSlots.filter((slot) => slot.kind === 'overflow').length
  ), 0);

  return {
    label: snapshot.projection?.label ?? 'No candidate selected',
    hasProjection: Boolean(snapshot.projection),
    plannedPlacements: snapshot.placements.length,
    projectedPlacements: snapshot.projection?.placements.length ?? 0,
    plannedBodyCount: plannedBodyIds.length,
    projectedBodyCount: projectedBodyIds.length,
    sharedBodyCount: sharedBodyIds.length,
    newBodyLabels: bodyLabelsForIds(newBodyIds, bodyById, system.name),
    plannedOnlyBodyLabels: bodyLabelsForIds(plannedOnlyBodyIds, bodyById, system.name),
    projectedOrbitalCount: laneCounts.orbital,
    projectedGroundCount: laneCounts.ground,
    projectedUnknownLaneCount: laneCounts.unknown,
    slotOverflowCount,
    economyDeltas: economyLedger.entries.map((entry) => ({
      economy: entry.economy,
      planned: entry.planned,
      projected: entry.projected,
      total: entry.total,
    })),
  };
}

function uniquePlacementBodyIds(placements: SimulateBuildPlacement[], bodies: SystemBody[]): string[] {
  const ids: string[] = [];
  placements.forEach((placement) => {
    const rawBodyId = placementBodyId(placement);
    if (!rawBodyId) return;
    const body = bodies.find((candidate) => sameBodyId(candidate.id, rawBodyId));
    const id = body?.id != null ? bodyIdKey(body.id) : rawBodyId;
    if (!ids.includes(id)) ids.push(id);
  });
  return ids;
}

function bodyLabelsForIds(ids: string[], bodyById: Map<string, SystemBody>, systemName?: string | null): string[] {
  return ids
    .map((id) => {
      const body = bodyById.get(id);
      return body ? compactBodyDisplayName(body, systemName) : 'Unknown body';
    })
    .slice(0, 4);
}

function bucketStructures(
  placements: SimulateBuildPlacement[],
  templatesById: Map<string, FacilityTemplate>,
  bodyById: Map<string, SystemBody>,
  projected: boolean,
  laneHints: Record<number, BodyPlannerLane> = {},
) {
  const buckets = new Map<string, ReturnType<typeof emptyStructureBuckets>>();

  placements.forEach((placement, index) => {
    const bodyId = placementBodyId(placement);
    if (!bodyId || !bodyById.has(bodyId)) return;
    const template = templatesById.get(placement.facility_template_id);
    const lane = plannerCanvasLaneForPlacement(template, bodyById.get(bodyId), laneHints[index]);
    const current = buckets.get(bodyId) ?? emptyStructureBuckets();
    current[lane].push({
      kind: 'placement',
      placement,
      template,
      index,
      projected,
      warningLabels: projected ? [] : missingPrerequisitesForPlacement(placement, placements, Array.from(templatesById.values())),
    });
    buckets.set(bodyId, current);
  });

  return buckets;
}

function emptyStructureBuckets() {
  return {
    orbital: [] as StructureBucketItem[],
    ground: [] as StructureBucketItem[],
    unassigned: [] as StructureBucketItem[],
  };
}

function emptyExistingStructureBuckets() {
  return {
    orbital: [] as ExistingStructure[],
    surface: [] as ExistingStructure[],
    unknown: [] as ExistingStructure[],
  };
}

function buildLaneSlots(
  bodyId: string,
  lane: PlannerCanvasLane,
  capacity: number | null,
  structures: PlannerStructureBucketItem[],
  body: SystemBody,
  system: SystemDetail,
): PlannerStructureSlot[] {
  if (capacity == null) {
    return [
      unknownSlot(bodyId, lane),
      ...structures.map((item, index) => structureSlot(bodyId, lane, item, index, body, system)),
    ];
  }

  if (capacity <= 0) {
    if (structures.length === 0) return [];
    return [
      ...structures.map((item, index) => structureSlot(bodyId, lane, item, index, body, system)),
      overflowSlot(bodyId, lane, structures.length),
    ];
  }

  const visible = structures.slice(0, capacity).map((item, index) => structureSlot(bodyId, lane, item, index, body, system));
  const empty = Array.from({ length: Math.max(0, capacity - visible.length) }, (_unused, index) => (
    emptySlot(bodyId, lane, visible.length + index)
  ));
  const overflow = structures.length > capacity ? [overflowSlot(bodyId, lane, structures.length - capacity)] : [];
  return [...visible, ...empty, ...overflow];
}

function structureSlot(
  bodyId: string,
  lane: PlannerCanvasLane,
  item: PlannerStructureBucketItem,
  index: number,
  body: SystemBody,
  system: SystemDetail,
): PlannerStructureSlot {
  if (item.kind === 'existing') {
    return existingStructureSlot(bodyId, lane, item, index, body, system);
  }
  const fullName = structureDisplayName(item.template, item.placement.facility_template_id);
  const directSegments = structureEconomySegments(item.template, item.projected);
  const baselineSegments = directSegments.length === 0 && item.template?.is_port
    ? stationBaselineSegments(item.template, body, system, item.projected)
    : [];
  const segments = directSegments.length > 0 ? directSegments : baselineSegments;
  const status = item.projected ? 'projected' : 'planned';
  const economyContext = contextualEconomyLabel(item.template);
  const prerequisiteWarnings = item.warningLabels;
  const directEconomyText = directSegments.map((segment) => {
    const strength = segment.strength == null ? 'CP generated unavailable' : `CP generated +${segment.strength}`;
    return `Direct facility economy: ${segment.economy} ${formatShare(segment.share)} | ${strength} | Source: catalogue/template`;
  }).join(' / ');
  const baselineEconomyText = baselineSegments.length > 0
    ? `Baseline (inherited/contextual): ${baselineSegments.map((segment) => `${segment.economy} ${formatShare(segment.share)}`).join(' / ')} | Source: ${baselineSegments[0]?.calculationSource ?? 'ED-Finder body economy profile'} | Run Preview for validated outcome.`
    : '';
  const economyText = directSegments.length > 0
    ? directEconomyText
    : baselineEconomyText
      ? economyContext
        ? `${economyContext} | ${baselineEconomyText}`
        : baselineEconomyText
      : economyContext ?? 'No economy metadata';

  return {
    id: `${bodyId}-${lane}-${status}-${item.index}-${item.placement.facility_template_id}-${index}`,
    kind: item.projected ? 'projected' : 'planned',
    label: `${item.projected ? 'Ghost ' : ''}${compactStructureName(fullName)}`,
    fullName,
    title: [
      `${fullName} | Status: ${item.projected ? 'Projected Suggested Build' : 'Planned Build Plan'} | ${economyText}`,
      prerequisiteWarnings.length > 0 ? `Missing prerequisite: ${prerequisiteWarnings.join('; ')}` : null,
    ].filter(Boolean).join(' | '),
    economySegments: segments,
    placementIndex: item.projected ? null : item.index,
    projectionIndex: item.projected ? item.index : null,
    existingStructureId: null,
    buildOrder: item.placement.build_order ?? null,
    status,
    economyContextLabel: economyContext,
    warningLabels: prerequisiteWarnings,
  };
}

function existingStructureSlot(
  bodyId: string,
  lane: PlannerCanvasLane,
  item: ExistingStructureBucketItem,
  index: number,
  body: SystemBody,
  system: SystemDetail,
): PlannerStructureSlot {
  const structure = item.structure;
  const directSegments = existingStructureEconomySegments(structure);
  const baselineSegments = directSegments.length === 0
    ? stationBaselineSegments(undefined, body, system, false)
    : [];
  const segments = directSegments.length > 0 ? directSegments : baselineSegments;
  const type = existingStructureDisplayType(structure);
  const bodyMatch = structure.association_status === 'inferred'
    ? `Body match inferred (${structure.association_source})`
    : structure.association_status === 'confirmed'
      ? `Body match confirmed (${structure.association_source})`
      : `Body match unresolved (${structure.association_source})`;
  const economyText = directSegments.length > 0
    ? `Existing direct economy: ${directSegments.map((segment) => segment.economy).join(' / ')} | Source: station economy metadata`
    : baselineSegments.length > 0
      ? `Baseline (inherited/contextual): ${baselineSegments.map((segment) => `${segment.economy} ${formatShare(segment.share)}`).join(' / ')} | Source: ${baselineSegments[0]?.calculationSource ?? 'ED-Finder body economy profile'} | Run Preview for validated outcome.`
      : 'No economy metadata';

  return {
    id: `${bodyId}-${lane}-existing-${structure.id}-${index}`,
    kind: 'existing',
    label: compactStructureName(structure.name),
    fullName: structure.name,
    title: [
      `${structure.name} | Existing ${type} | ${economyText}`,
      structure.pad_size ? `Pad ${structure.pad_size}` : null,
      bodyMatch,
      structure.body_match_reason,
    ].filter(Boolean).join(' | '),
    economySegments: segments,
    placementIndex: null,
    projectionIndex: null,
    existingStructureId: structure.id,
    buildOrder: null,
    status: 'existing',
    economyContextLabel: baselineSegments.length > 0 ? 'Economy: inherited/contextual baseline - run Preview for validated outcome.' : null,
    warningLabels: structure.association_status === 'inferred' ? ['Inferred association'] : [],
    trustStatus: structure.association_status,
  };
}

function unknownSlot(bodyId: string, lane: PlannerCanvasLane): PlannerStructureSlot {
  return {
    id: `${bodyId}-${lane}-unknown`,
    kind: 'unknown',
    label: '?',
    fullName: 'Slot prediction unavailable',
    title: 'Slot prediction unavailable for this body and lane.',
    economySegments: [],
    placementIndex: null,
    projectionIndex: null,
    existingStructureId: null,
    buildOrder: null,
    status: 'unknown',
    economyContextLabel: null,
    warningLabels: [],
  };
}

function emptySlot(bodyId: string, lane: PlannerCanvasLane, index: number, label = ''): PlannerStructureSlot {
  return {
    id: `${bodyId}-${lane}-empty-${index}`,
    kind: 'empty',
    label,
    fullName: 'Empty slot',
    title: 'Empty slot',
    economySegments: [],
    placementIndex: null,
    projectionIndex: null,
    existingStructureId: null,
    buildOrder: null,
    status: 'unknown',
    economyContextLabel: null,
    warningLabels: [],
  };
}

function overflowSlot(bodyId: string, lane: PlannerCanvasLane, count: number): PlannerStructureSlot {
  const laneLabel = lane === 'ground' ? 'Surface' : 'Orbital';
  return {
    id: `${bodyId}-${lane}-overflow-${count}`,
    kind: 'overflow',
    label: `+${count} over`,
    fullName: `${laneLabel} capacity exceeded: +${count} over predicted slots`,
    title: `${laneLabel} capacity exceeded: ${count} more occupied structure${count === 1 ? '' : 's'} than predicted slots. Verify in Architect Mode.`,
    economySegments: [],
    placementIndex: null,
    projectionIndex: null,
    existingStructureId: null,
    buildOrder: null,
    status: 'unknown',
    economyContextLabel: null,
    warningLabels: [`${laneLabel} capacity exceeded`],
  };
}

function stationBaselineSegments(
  _template: FacilityTemplate | undefined,
  body: SystemBody | null | undefined,
  system: SystemDetail | null | undefined,
  projected: boolean,
): PlannerEconomySegment[] {
  void system;
  const baseline = calculateStationBaselineEconomy(body ?? null);
  if (baseline.segments.length === 0) return [];
  const description = describeStationBaselineEconomy(baseline, { projected });
  return baseline.segments.map((segment) => ({
    economy: segment.economy,
    share: segment.percent,
    strength: null,
    projected,
    inherited: true,
    calculationSource: baseline.calculationSource,
    caveats: description ? [description, ...baseline.caveats] : baseline.caveats,
  }));
}

export function structureEconomySegments(template: FacilityTemplate | undefined, projected: boolean): PlannerEconomySegment[] {
  const economy = normalisePlanningEconomy(template?.economy);
  if (!economy) return [];
  const yellow = typeof template?.yellow_cp_generated === 'number' ? template.yellow_cp_generated : 0;
  const green = typeof template?.green_cp_generated === 'number' ? template.green_cp_generated : 0;
  const strength = template ? Math.max(0, yellow + green) : null;
  return [{
    economy,
    share: 100,
    strength,
    projected,
    calculationSource: 'catalogue/template economy metadata',
  }];
}

function existingStructureEconomySegments(structure: ExistingStructure): PlannerEconomySegment[] {
  const economy = normalisePlanningEconomy(structure.economy);
  if (!economy) return [];
  return [{
    economy,
    share: 100,
    strength: null,
    projected: false,
    calculationSource: 'station economy metadata',
  }];
}

export function structureDisplayName(template: FacilityTemplate | undefined, fallback: string): string {
  return template ? templateDisplayName(template) : readableTemplateId(fallback);
}

function compactStructureName(name: string): string {
  const clean = name.trim();
  if (!clean) return 'Structure';
  const words = clean.split(/\s+/);
  if (words.length === 1) return clean;
  if (words.length === 2 && [
    'Starport',
    'Outpost',
    'Installation',
    'Dome',
    'Relay',
    'Port',
  ].includes(words[1])) return words[0];
  if (words.length === 2) return clean;
  const facilityIndex = words.findIndex((word) => [
    'Starport',
    'Hub',
    'Outpost',
    'Lab',
    'Installation',
    'Dome',
    'Relay',
    'Port',
  ].includes(word));
  if (facilityIndex > 0) return `${words[facilityIndex - 1]} ${words[facilityIndex]}`;
  return words.slice(0, 2).join(' ');
}

function readableTemplateId(value: string): string {
  return value
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

export function plannerCanvasLaneForPlacement(
  template: FacilityTemplate | undefined,
  body: SystemBody | undefined,
  laneHint?: BodyPlannerLane | null,
): PlannerCanvasLane {
  const lane = placementLaneForTemplate(template, body, laneHint);
  if (lane === 'surface') return 'ground';
  return lane;
}

export function placementBodyId(placement: SimulateBuildPlacement | undefined): string | null {
  return placement?.local_body_id != null ? bodyIdKey(placement.local_body_id) : null;
}

function flattenBodyNodes(bodies: SystemBody[], systemName?: string | null): FlatBodyNode[] {
  const nodes = buildBodyTree(bodies, systemName);
  const flat: FlatBodyNode[] = [];
  const visit = (node: BodyNode, depth: number, guide: boolean[], isLast: boolean) => {
    flat.push({ body: node.body, id: node.id, depth, guide, isLast });
    node.children.forEach((child, index) => {
      visit(child, depth + 1, [...guide, !isLast], index === node.children.length - 1);
    });
  };
  nodes.forEach((node, index) => visit(node, 0, [], index === nodes.length - 1));
  return flat;
}

function buildBodyTree(bodies: SystemBody[], systemName?: string | null): BodyNode[] {
  const nodes = bodies
    .filter((body) => body.id != null)
    .map((body) => ({ body, id: bodyIdKey(body.id), children: [] as BodyNode[] }));
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const roots: BodyNode[] = [];

  nodes.forEach((node) => {
    const parentId = bodyParentId(node.body);
    const parent = parentId ? byId.get(parentId) : null;
    if (parent) parent.children.push(node);
    else roots.push(node);
  });

  const sort = (items: BodyNode[]) => {
    items.sort((left, right) => bodyRank(left.body) - bodyRank(right.body)
      || compareBodiesByHierarchy(left.body, right.body, systemName));
    items.forEach((item) => sort(item.children));
  };

  sort(roots);
  return roots;
}

function bodyRank(body: SystemBody) {
  if (body.body_type === 'Star') return 0;
  if (body.body_type === 'Planet') return bodyParentId(body) ? 2 : 1;
  return 3;
}

export function bodyKind(body: SystemBody): string {
  const subtype = body.subtype?.replace(/\bworld\b/i, '').trim();
  const type = subtype || body.body_type || 'Body';
  const flags = [
    body.is_landable ? 'landable' : null,
    body.is_water_world ? 'water' : null,
    body.is_terraformable ? 'terraformable' : null,
  ].filter(Boolean);
  return flags.length > 0 ? `${type} / ${flags.join(' / ')}` : type;
}

export function bodyMarker(body: SystemBody) {
  const text = `${body.body_type ?? ''} ${body.subtype ?? ''}`.toLowerCase();
  if (body.body_type === 'Star') return BODY_MARKER_COLORS.star;
  if (text.includes('gas')) return BODY_MARKER_COLORS.gas;
  if (body.is_water_world || body.is_terraformable) return BODY_MARKER_COLORS.earth;
  if (bodyParentId(body)) return BODY_MARKER_COLORS.moon;
  return BODY_MARKER_COLORS.rock;
}

function countRowWarnings(
  body: SystemBody,
  prediction: BodySlotPrediction | null,
  ledger: PlanningEconomyLedger,
  overflowCount = 0,
) {
  let count = 0;
  if (!prediction) count += 1;
  if (ledger.unknownCount > 0) count += ledger.unknownCount;
  if (bodyTags(body).includes('Unknown body data')) count += 1;
  if (overflowCount > 0) count += overflowCount;
  return count;
}
