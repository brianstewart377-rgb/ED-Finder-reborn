import type { ExistingStructure } from './existingInfrastructure';
import type { BodyPlannerLane } from './plannerTypes';
import type { VisiblePlannerCanvasLane } from './plannerCanvasTypes';

export function existingAssociationLabel(structure: ExistingStructure): string {
  if (structure.transient) return 'transient';
  if (structure.association_status === 'unresolved') return 'unresolved';
  if (structure.lane === 'unknown') return 'lane unknown';
  if (structure.association_status === 'inferred') return 'verify';
  return 'confirmed';
}

export function plannerCanvasLaneToPlannerLane(
  lane: VisiblePlannerCanvasLane,
): BodyPlannerLane {
  return lane === 'ground' ? 'surface' : 'orbital';
}

export function formatShare(value: number): string {
  return Number.isInteger(value) ? `${value}%` : `${value.toFixed(1)}%`;
}
