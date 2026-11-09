import type { FacilityTemplate, SimulateBuildPlacement } from '@ed-finder/api-client/types';
import { existingStructureDisplayType, type ExistingStructure } from './existingInfrastructure';

export interface SlotLabelItem {
  placement: SimulateBuildPlacement;
  index: number;
  template: FacilityTemplate | undefined;
  lane?: 'orbital' | 'surface' | 'unassigned';
}

export function slotLabel(item: SlotLabelItem) {
  const raw = item.template?.name ?? item.placement.facility_template_id;
  return compactLabel(raw);
}

export function existingSlotLabel(structure: ExistingStructure) {
  return compactLabel(existingStructureDisplayType(structure));
}

export function compactLabel(raw: string) {
  const clean = raw.replace(/[^A-Za-z0-9 ]/g, ' ').trim();
  if (!clean) return '??';
  const words = clean.split(/\s+/).slice(0, 2);
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return `${words[0][0] ?? ''}${words[1][0] ?? ''}`.toUpperCase();
}

export function slotTitle(item: { template?: FacilityTemplate | undefined; placement: SimulateBuildPlacement }) {
  return item.template?.name ?? item.placement.facility_template_id;
}
