import type { SystemBody } from '@ed-finder/api-client/types';
import type { PlanningEconomyLedger, PlanningEconomyName } from './planningEconomy';

export type PlannerCanvasLane = 'orbital' | 'ground' | 'unassigned';
export type VisiblePlannerCanvasLane = Exclude<PlannerCanvasLane, 'unassigned'>;
export type PlannerSlotKind = 'empty' | 'existing' | 'planned' | 'projected' | 'unknown' | 'overflow';
export type ProjectionComparisonView = 'bodies' | 'economy' | 'slots';

export interface PlannerEconomySegment {
  economy: PlanningEconomyName;
  share: number;
  strength: number | null;
  projected: boolean;
  inherited?: boolean;
  calculationSource?: string;
  caveats?: string[];
}

export interface PlannerStructureSlot {
  id: string;
  kind: PlannerSlotKind;
  label: string;
  fullName: string;
  title: string;
  economySegments: PlannerEconomySegment[];
  placementIndex: number | null;
  projectionIndex: number | null;
  existingStructureId: string | null;
  buildOrder: number | null;
  status: 'existing' | 'planned' | 'projected' | 'unknown';
  economyContextLabel: string | null;
  warningLabels: string[];
  trustStatus?: 'confirmed' | 'inferred' | 'unresolved';
}

export interface PlannerLaneOccupancySummary {
  capacity: number | null;
  existingCount: number;
  inferredExistingCount: number;
  plannedCount: number;
  projectedCount: number;
  remainingForPlan: number | null;
  projectedOverflowCount: number;
}

export interface PlannerCanvasRow {
  id: string;
  body: SystemBody;
  depth: number;
  isLast: boolean;
  guide: boolean[];
  displayName: string;
  compactName: string;
  bodyKind: string;
  bodyTags: string[];
  orbitalCapacity: number | null;
  groundCapacity: number | null;
  orbitalCapacityEstimated: boolean;
  groundCapacityEstimated: boolean;
  orbitalSlots: PlannerStructureSlot[];
  groundSlots: PlannerStructureSlot[];
  unassignedSlots: PlannerStructureSlot[];
  bodyEconomy: PlanningEconomyLedger;
  projected: boolean;
  warningCount: number;
  existingCount: number;
  inferredExistingCount: number;
  plannedCount: number;
  projectedCount: number;
  emptySlotCount: number;
  orbitalAddDisabledReason: string | null;
  groundAddDisabledReason: string | null;
  orbitalOccupancy: PlannerLaneOccupancySummary;
  groundOccupancy: PlannerLaneOccupancySummary;
}

export interface ProjectionEconomyDelta {
  economy: PlanningEconomyName;
  planned: number;
  projected: number;
  total: number;
}

export interface ProjectionComparisonSummary {
  label: string;
  hasProjection: boolean;
  plannedPlacements: number;
  projectedPlacements: number;
  plannedBodyCount: number;
  projectedBodyCount: number;
  sharedBodyCount: number;
  newBodyLabels: string[];
  plannedOnlyBodyLabels: string[];
  projectedOrbitalCount: number;
  projectedGroundCount: number;
  projectedUnknownLaneCount: number;
  slotOverflowCount: number;
  economyDeltas: ProjectionEconomyDelta[];
}

export interface TelemetryFact<TValue = unknown> {
  label: string;
  value: TValue;
  tone?: 'silver' | 'orange' | 'green' | 'gold';
}
