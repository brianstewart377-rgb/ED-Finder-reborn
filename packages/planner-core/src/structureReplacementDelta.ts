import type { FacilityTemplate } from '@ed-finder/api-client/types';
import { formatLocation } from './formatters';

export interface ReplacementFieldDelta {
  label: string;
  currentValue: string;
  proposedValue: string;
  changed: boolean;
  warnCurrent: boolean;
  warnProposed: boolean;
}

export interface WarningDeltas {
  added: string[];
  removed: string[];
  unchanged: string[];
}

export function buildReplacementFieldDeltas(
  currentTemplate: FacilityTemplate | undefined,
  proposedTemplate: FacilityTemplate,
): ReplacementFieldDelta[] {
  return [
    buildDelta('Tier', currentTemplate?.tier != null ? String(currentTemplate.tier) : 'Unknown', String(proposedTemplate.tier), !currentTemplate, false),
    buildDelta('Allowed location', currentTemplate ? formatLocation(currentTemplate.allowed_location) : 'Unknown', formatLocation(proposedTemplate.allowed_location), !currentTemplate, false),
    buildDelta('Pad size', currentTemplate?.pad_size ?? 'Unknown', proposedTemplate.pad_size ?? 'Unknown', !currentTemplate?.pad_size, !proposedTemplate.pad_size),
    buildDelta('Economy', currentTemplate?.economy ?? 'Unknown', proposedTemplate.economy ?? 'Unknown', !currentTemplate?.economy, !proposedTemplate.economy),
    buildDelta('Role', currentTemplate?.category ?? 'Unknown', proposedTemplate.category ?? 'Unknown', !currentTemplate?.category, !proposedTemplate.category),
    buildDelta(
      'CP gives',
      currentTemplate ? `Y+${currentTemplate.yellow_cp_generated} G+${currentTemplate.green_cp_generated}` : 'Unknown',
      `Y+${proposedTemplate.yellow_cp_generated} G+${proposedTemplate.green_cp_generated}`,
      !currentTemplate,
      false,
    ),
    buildDelta(
      'CP needs',
      currentTemplate ? `Y${currentTemplate.yellow_cp_cost} G${currentTemplate.green_cp_cost}` : 'Unknown',
      `Y${proposedTemplate.yellow_cp_cost} G${proposedTemplate.green_cp_cost}`,
      !currentTemplate,
      false,
    ),
    buildDelta(
      'Confidence',
      currentTemplate?.confidence ?? 'missing',
      proposedTemplate.confidence ?? 'missing',
      !currentTemplate || currentTemplate.confidence === 'estimated',
      proposedTemplate.confidence === 'estimated',
    ),
  ];
}

export function buildWarningDeltas(currentWarnings: string[], proposedWarnings: string[]): WarningDeltas {
  const currentSet = new Set(currentWarnings);
  const proposedSet = new Set(proposedWarnings);

  return {
    added: proposedWarnings.filter((warning) => !currentSet.has(warning)),
    removed: currentWarnings.filter((warning) => !proposedSet.has(warning)),
    unchanged: proposedWarnings.filter((warning) => currentSet.has(warning)),
  };
}

function buildDelta(
  label: string,
  currentValue: string,
  proposedValue: string,
  warnCurrent: boolean,
  warnProposed: boolean,
): ReplacementFieldDelta {
  return {
    label,
    currentValue,
    proposedValue,
    changed: currentValue !== proposedValue,
    warnCurrent,
    warnProposed,
  };
}
